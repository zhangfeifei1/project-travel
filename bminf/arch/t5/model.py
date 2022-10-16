import threading
from typing import List, Union, Optional
import cupy
from ..seq2seq import Seq2SeqModel
from ...layers.transformer_block import TransformerBlockDecoder, TransformerBlockEncoder
from ...layers.encoder_kv import EncoderKeyValueProjection
from ...layers.position_bias import PositionBias
from ...layers.embedding import Embedding
from ...layers.layer_norm import LayerNorm
from ...layers.mask import InputMask
from ...layers.lm_head import LMHead
from ...layers.layer_list import LayerList
from .config import T5Configuration
from .tokenizer import T5Tokenizer
from .context import T5InferenceContext
from ...allocator import ReusedAllocator, SizeLimitedAllocator
import numpy as np
import logging
from ... import data

logger = logging.getLogger(__name__)

class T5(Seq2SeqModel):
    def __init__(self, config : T5Configuration):
        # Build Model
        logger.info("Building model")
        
        self.memory_overlap = config.MEMORY_OVERLAP
        self.max_overlap_layers = max(config.NUM_ENCODER_LAYERS, config.NUM_DECODER_LAYERS)
        if self.memory_overlap:
            self.overlap_layers = min(config.OVERLAP_LAYERS, self.max_overlap_layers)
        else:
            self.overlap_layers = self.max_overlap_layers

        self.encoder_only = config.ENCODER_ONLY
        self.max_decoder_length = config.MAX_DECODER_LENGTH
        self.dim_model = config.DIM_MODEL

        logger.info("============ T5 ==============")
        logger.info("MEM_OVERLAP: %s", self.memory_overlap)
        logger.info("OVERLAP_LAYERS: %s", self.overlap_layers)
        logger.info("ENCODER_ONLY: %s", self.encoder_only)
        logger.info("MAX_DECODER_LENGTH: %s", self.max_decoder_length)

        self.input_embedding = Embedding(config.VOCAB_SIZE, config.DIM_MODEL)
        self.input_mask = InputMask(is_decoder=False)

        self.encoder_position_bias = PositionBias(config.NUM_POSITION_BUCKETS, config.NUM_HEADS, is_decoder=False)
        self.num_encoder = config.NUM_ENCODER_LAYERS
        self.encoder = LayerList([
            TransformerBlockEncoder(config.DIM_MODEL, config.DIM_FF, config.DIM_KV, config.NUM_HEADS)
                for _ in range(config.NUM_ENCODER_LAYERS)
        ])
        self.encoder_final_layer_nrom = LayerNorm(config.DIM_MODEL)
        self.num_heads = config.NUM_HEADS
        self.dim_qkv = config.DIM_KV

        if not self.encoder_only:
            self.decoder_position_bias = PositionBias(config.NUM_POSITION_BUCKETS, config.NUM_HEADS, is_decoder=True)
            self.encoder_kv = EncoderKeyValueProjection(config.NUM_DECODER_LAYERS, config.DIM_MODEL, config.DIM_KV, config.NUM_HEADS)
            self.lm_head = LMHead(config.VOCAB_SIZE, config.DIM_MODEL)
            self.num_decoder = config.NUM_DECODER_LAYERS
            self.decoder = LayerList([
                TransformerBlockDecoder(config.DIM_MODEL, config.DIM_FF, config.DIM_KV, config.NUM_HEADS)
                    for _ in range(config.NUM_DECODER_LAYERS)
            ])
            self.decoder_final_layer_nrom = LayerNorm(config.DIM_MODEL)

        if config.MODEL_NAME is not None:
            # init parameter

            model_path = data.ensure_file(config.MODEL_NAME, "checkpoint.pt")
            vocab_path = data.ensure_file(config.MODEL_NAME, "vocab.txt")

            self.tokenizer = T5Tokenizer(vocab_path)

            self.device = config.DEVICE
            with self.device:
                logger.info("Start loading parameters from disk to cpu")
                self.load( open(model_path, "rb") )

                logger.info("Start loading parameters from cpu to gpu")
                
                load_stream = cupy.cuda.Stream()
                if self.memory_overlap:
                    mx_size = 0
                    for i in range(config.NUM_ENCODER_LAYERS):
                        mx_size = max(self.encoder[i].nbytes, mx_size)
                    for i in range(config.NUM_DECODER_LAYERS):
                        mx_size = max(self.decoder[i].nbytes, mx_size)

                    if self.overlap_layers >= self.max_overlap_layers:
                        overlap_size = mx_size * self.max_overlap_layers * 2
                    elif self.overlap_layers * 2 >= self.max_overlap_layers:
                        overlap_size = mx_size * self.overlap_layers * 2 + (self.max_overlap_layers - self.overlap_layers) * mx_size
                    elif self.overlap_layers * 3 >= self.max_overlap_layers:
                        overlap_size = mx_size * self.overlap_layers * 3 + (self.max_overlap_layers - self.overlap_layers * 2) * mx_size
                    else:
                        overlap_size = mx_size * self.overlap_layers * 4

                    other_size = self.nbytes - self.encoder.nbytes - self.decoder.nbytes

                    logger.info("Using overlap loader: overlap_size %d, other_size: %d, dynamic_memory %d, memory_limit %d", overlap_size, other_size, config.DYNAMIC_MEMORY, config.MEMORY_LIMIT)
                    if overlap_size + other_size + config.DYNAMIC_MEMORY > config.MEMORY_LIMIT:
                        raise ValueError("memory limit not enough, at least %d bytes, but got %d bytes" % (overlap_size + other_size + config.DYNAMIC_MEMORY, config.MEMORY_LIMIT))
                    self.parameter_allocator = ReusedAllocator(other_size + (mx_size * self.overlap_layers * 2))

                    if self.overlap_layers >= self.max_overlap_layers:
                        self.overlap_allocator = [None, None]
                    elif self.overlap_layers * 2 >= self.max_overlap_layers:
                        self.overlap_allocator = [None, ReusedAllocator( (self.max_overlap_layers - self.overlap_layers) * mx_size )]
                    elif self.overlap_layers * 3 >= self.max_overlap_layers:
                        self.overlap_allocator = [ReusedAllocator( (self.max_overlap_layers - self.overlap_layers * 2) * mx_size ), ReusedAllocator( self.overlap_layers * mx_size )]
                    else:
                        self.overlap_allocator = [ReusedAllocator( self.overlap_layers * mx_size ), ReusedAllocator( self.overlap_layers * mx_size )]
                    self.overlap_allocator_status = [None, None]
                    self.variable_allocator = SizeLimitedAllocator(config.MEMORY_LIMIT - other_size - overlap_size)

                    for name, layer in self._sub_layers.items():
                        if name in ["encoder", "decoder"]:
                            # move first overlap_size layers to device
                            for i in range(min(self.overlap_layers, len(layer))):
                                layer[i].to_device( self.parameter_allocator, load_stream )
                        else:
                            layer.to_device( self.parameter_allocator, load_stream  )
                else:
                    if self.nbytes + config.DYNAMIC_MEMORY > config.MEMORY_LIMIT:
                        raise ValueError("memory limit not enough, at least %d bytes, but got %d bytes" % (self.nbytes + config.DYNAMIC_MEMORY, config.MEMORY_LIMIT))
                    
                    logger.info("Using static loader: total: %d, dynamic_memory %d, memory_limit %d", self.nbytes, config.DYNAMIC_MEMORY, config.MEMORY_LIMIT)
                    self.parameter_allocator = ReusedAllocator(self.nbytes)
                    self.variable_allocator = SizeLimitedAllocator(config.MEMORY_LIMIT - self.nbytes)

                    self.to_device(self.parameter_allocator, load_stream)
                
                self.device.synchronize()
                self.load_stream = cupy.cuda.Stream(non_blocking=True)
                self.calc_stream = cupy.cuda.Stream(non_blocking=True)
                with self.calc_stream:
                    self.variable_allocator.alloc(config.DYNAMIC_MEMORY) # preallocate
                self.device.synchronize()

            logger.info("Cleaning useless parameters on cpu")
            if self.memory_overlap:
                for name, layer in self._sub_layers.items():
                    if name in ["encoder", "decoder"]:
                        # move first overlap_size layers to device
                        pass
                    else:
                        layer._remove_data()
                
                for i in range(self.max_overlap_layers):
                    if i < self.overlap_layers:
                        self.encoder[i]._remove_data()
                        self.decoder[i]._remove_data()
                    else:
                        if i < self.num_encoder:
                            self.encoder[i]._try_pinned()
                        if i < self.num_decoder:
                            self.decoder[i]._try_pinned()
            else:
                self._remove_data()
            logger.info("End of model initialization")

    def encode_loader(self, barrier, load_stream):
        with self.device:
            for i in range(self.num_encoder):
                if i % self.overlap_layers == 0:
                    load_stream.synchronize()
                    barrier.wait()
                    # sync here

                    if i + self.overlap_layers < self.num_encoder:
                        overlap_idx = ((i + self.overlap_layers) // self.overlap_layers) % 2
                        if self.overlap_allocator_status[overlap_idx] == i + 1:
                            continue
                        else:
                            olp_allocator = self.overlap_allocator[overlap_idx]
                            olp_allocator.reset()
                            for j in range(i + self.overlap_layers, min(i + self.overlap_layers * 2, self.num_encoder)):
                                logger.info("Load encoder layer %d", j)
                                self.encoder[j].to_device(olp_allocator, load_stream)
                            self.overlap_allocator_status[overlap_idx] = i + 1

    def decode_loader(self, barrier, load_stream):
        with self.device:
            for i in range(self.num_decoder):
                if i % self.overlap_layers == 0:
                    load_stream.synchronize()
                    barrier.wait()
                    # sync here

                    if i + self.overlap_layers < self.num_decoder:
                        overlap_idx = ((i + self.overlap_layers) // self.overlap_layers) % 2
                        if self.overlap_allocator_status[overlap_idx] == -(i + 1):
                            continue
                        else:
                            olp_allocator = self.overlap_allocator[overlap_idx]
                            olp_allocator.reset()
                            for j in range(i + self.overlap_layers, min(i + self.overlap_layers * 2, self.num_decoder)):
                                logger.info("Load decoder layer %d", j)
                                self.decoder[j].to_device(olp_allocator, load_stream)
                            self.overlap_allocator_status[overlap_idx] = -(i + 1)


    def encode(self, input_idx : np.ndarray, input_length : List[int]):
        barrier = threading.Barrier(2)
        load_thread = threading.Thread(target=self.encode_loader, args=(barrier, self.load_stream), daemon=True)
        load_thread.start()
        with self.device:
            calc_stream = self.calc_stream

            batch_size, seq_len = input_idx.shape
            with calc_stream:
                x = self.input_embedding.forward(self.variable_allocator, input_idx)
                encoder_attn_mask = self.input_mask.forward(self.variable_allocator, input_length, seq_len)
                x = x.transpose((0, 2, 1))
                assert x.dtype == cupy.float16

                x_pos = self.encoder_position_bias.forward(self.variable_allocator, seq_len, seq_len)
                assert x_pos.shape == (1, self.num_heads, seq_len, seq_len)
                assert x_pos.dtype == cupy.float16

            for i in range(self.num_encoder):
                if i % self.overlap_layers == 0:
                    calc_stream.synchronize()
                    barrier.wait()
                    barrier.reset()
                    # sync

                logger.info("Calc encoder layer %d", i)
                with calc_stream:
                    x = self.encoder[i].forward(
                        self.variable_allocator, 
                        x,
                        encoder_attn_mask,
                        x_pos,
                        True
                    )
            with calc_stream:
                x = self.encoder_final_layer_nrom.forward(self.variable_allocator, x)
            calc_stream.synchronize()
            load_thread.join()
            return T5InferenceContext(x, input_length)    # (batch, dim_model, seq_len)
    
    def _init_decoder_context(self, ctx : T5InferenceContext):
        hidden_state = ctx.hidden_states
        input_length = ctx.input_length
        
        if self.encoder_only:
            raise ValueError("T5-encoder only")
        with self.device:
            with self.calc_stream:
                batch_size, _, seq_ipt_len = hidden_state.shape

                # (batch, num_decoder, 2, num_heads, dim_kv, seq_ipt_len),
                encoder_layers_kv = self.encoder_kv.forward(self.variable_allocator, hidden_state)

                # (1, num_heads, max_decoder_length, max_decoder_length)
                dec_pos = self.decoder_position_bias.forward(
                    self.variable_allocator,
                    self.max_decoder_length,
                    self.max_decoder_length
                )

                past_kv = self.variable_allocator.alloc_array((self.num_decoder, 2, batch_size, self.num_heads, self.dim_qkv, self.max_decoder_length), dtype=cupy.float16)
                past_kv[:] = 0
                
                encoder_mask = self.input_mask.forward(self.variable_allocator, input_length, seq_ipt_len)[:, :, 0]


                ctx.encoder_layers_kv = encoder_layers_kv
                ctx.decoder_position_bias = dec_pos
                ctx.past_kv = past_kv
                ctx.encoder_mask = encoder_mask
                ctx.step_pos = 0

    def decode_step(self,
            ctx : T5InferenceContext,
            inputs : Union[List[int], np.ndarray]
        ) -> cupy.ndarray:

        past_kv = ctx.past_kv
        encoder_layers_kv = ctx.encoder_layers_kv
        dec_position_bias = ctx.decoder_position_bias
        encoder_mask = ctx.encoder_mask
        step_input = inputs
        step_pos = ctx.step_pos
        ctx.step_pos += 1
    
        barrier = threading.Barrier(2)
        load_thread = threading.Thread(target=self.decode_loader, args=(barrier, self.load_stream), daemon=True)
        load_thread.start()

        with self.device:
            calc_stream = self.calc_stream

            with calc_stream:
                x = self.input_embedding.forward(self.variable_allocator, step_input)    # (batch, dim_model)
            for i in range(self.num_decoder):
                if i % self.overlap_layers == 0:
                    calc_stream.synchronize()
                    barrier.wait()
                    barrier.reset()
                    # sync
                logger.info("Calc decoder layer %d", i)

                with calc_stream:
                    x = self.decoder[i].forward(
                        self.variable_allocator,
                        x,                          # (batch, dim_model)
                        past_kv[i],                 # (2, batch, num_heads, dim_kv, max_decoder_length)
                        step_pos,                   # 1
                        encoder_mask,               # (batch, seq_ipt_len)
                        encoder_layers_kv[i],       # (2, batch, num_heads, dim_kv, seq_ipt_len)
                        dec_position_bias,          # (1, num_heads, max_decoder_length, max_decoder_length)
                        True
                    )
            with calc_stream:
                x = self.decoder_final_layer_nrom.forward(self.variable_allocator, x[:, :, cupy.newaxis])[:, :, 0]
                x = self.lm_head.forward(self.variable_allocator, x)
            calc_stream.synchronize()
            load_thread.join()
            return x
    
    def _text_to_id(self, sentence):
        return self.tokenizer.encode(sentence)

    def _id_to_text(self, idx : List[int]):
        return self.tokenizer.decode(idx)
    
    def _get_token_id(self, token, use_unk):
        token = token.translate(self.tokenizer.translator_enc)
        if use_unk:
            return self.tokenizer.encoder.get(token, self.tokenizer.unk_id)
        else:
            return self.tokenizer.encoder.get(token, None)
    
    def _get_id_token(self, idx):
        return self.tokenizer.decoder[idx].translate(self.tokenizer.translator_dec)
    