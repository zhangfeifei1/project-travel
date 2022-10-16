from typing import Optional, Tuple, Union, List
from ..arch.t5 import T5Configuration, T5
import cupy
import numpy as np
from ..utils.sampler import GenerateSampler

import logging
logger = logging.getLogger(__name__)

SPAN_TOKEN = "<span>"

class CPM2Configuration(T5Configuration):
    MODEL_NAME = "cpm2.1"

class CPM2(T5):
    def __init__(self, device : Union[None, int, cupy.cuda.Device] = None, memory_limit : Optional[int] = None, config : Optional[CPM2Configuration] = None):
        """Model CPM-2: Large-scale Cost-effective Pre-trained Language Models
        
        `[Repo] <https://github.com/TsinghuaAI/CPM-2-Pretrain>`__
        `[PDF] <https://arxiv.org/abs/2106.10715>`__

        Args:
            device: Index of CUDA device or ``None``.
            memory_limit: Total memory limit for this model in bytes.
            config: A CPM2 configuration object.

        """
        if config is None:
            config = CPM2Configuration()

        if config.DEVICE is None:
            if device is None:
                device = 0
            if isinstance(device, int):
                device = cupy.cuda.Device(device)
            config.DEVICE = device

        if config.MEMORY_LIMIT is None:
            if memory_limit is None:
                # free - 100MB
                memory_limit = config.DEVICE.mem_info[0] - 100 * 1024 * 1024
            config.MEMORY_LIMIT = memory_limit
        
        if config.MEMORY_OVERLAP:
            if config.OVERLAP_LAYERS is None:
                max_overlap = max(config.NUM_ENCODER_LAYERS, config.NUM_DECODER_LAYERS)
                max_layers = (config.MEMORY_LIMIT - config.DYNAMIC_MEMORY - 1235640320) // 226615296

                logger.info("Auto overlap layers: (max_layers: %d, max_overlap: %d)", max_layers, max_overlap)
                if max_layers * 3 < max_overlap * 4:
                    config.OVERLAP_LAYERS = max_layers // 4
                elif max_layers < max_overlap * 2:
                    config.OVERLAP_LAYERS = max_layers - max_overlap
                else:
                    config.OVERLAP_LAYERS = max_overlap
                logger.info("Auto overlap layers: result %d", config.OVERLAP_LAYERS)
                if config.OVERLAP_LAYERS < 1:
                    raise ValueError("Memory is not enough")

        super().__init__(config)

    def pre_processing(self,
                input_sentence : str,
                spans_position : Optional[List[int]] = None,
                max_tokens : int = 128,
                top_n : Optional[int] = None,
                top_p : Optional[float] = None,
                temperature : float = 0.9,
                frequency_penalty : float = 0,
                presence_penalty : float = 0,
                start_span_idx : int = 0,
        ):
        
        if spans_position is None:
            spans_position = []
            st = 0
            while True:
                nw_pos = input_sentence.find(SPAN_TOKEN, st)
                if nw_pos == -1:
                    break
                spans_position.append(nw_pos)
                st = nw_pos + len(SPAN_TOKEN)
        if len(spans_position) == 0:
            raise ValueError("No spans")
        if len(spans_position) > 16:
            raise ValueError("Too many spans")
        for pos in spans_position:
            if not input_sentence[pos:].startswith(SPAN_TOKEN):
                raise ValueError("Wrong span token at position %d" % pos)
        
        idx = []
        span_idx = start_span_idx
        last_pos = 0
        for pos in spans_position:
            idx += self.text_to_id(input_sentence[last_pos: pos])
            idx += [ self.tokenizer.get_span(span_idx) ]
            span_idx += 1
            last_pos = pos + len(SPAN_TOKEN)

        idx += self.text_to_id(input_sentence[last_pos:])
        input_length = len(idx)

        ctx = self.encode(np.array([idx], dtype=np.int64), [input_length])
        self.init_decoder_context(ctx)
        
        sampler = GenerateSampler(
            idx, 
            self.tokenizer.vocab_size,
            self.device,
            max_tokens,
            top_n,
            top_p,
            temperature,
            frequency_penalty,
            presence_penalty
        )

        return ctx, sampler, spans_position


    def fill_blank(self, 
            input_sentence : str,
            spans_position : Optional[List[int]] = None,
            max_tokens : int = 128,
            top_n : Optional[int] = None,
            top_p : Optional[float] = None,
            temperature : float = 0.9,
            frequency_penalty : float = 0,
            presence_penalty : float = 0,
        ):
        """Generate spans from input sentence.

        Args:
            input_sentence: Input sentence with "<span>" tokens.
            spans_position: List of span positions. If ``None``, the positions of span are automatically detected.
            max_tokens: Maximum number of tokens to generate.
            top_n: Only sampling from top n tokens in the result.
            top_p: Only sampling from tokens that comprising the top p probability in the result.
            temperature: Temperature for sampling. Higher values mean more diverse results. 
            frequency_penalty: A penalty used to avoid models generating the same content.
            presence_penalty: A penalty used to avoid models generating the same topic.
        
        Returns:
            A list of generated spans, including positions and contents.
        """
        # Input: ... <s_0> ... <s_1> ... <s_2> ...
        # Output: <s> <s_0> ... <s_1> ... <s_2> ...

        ctx, sampler, spans_position = self.pre_processing(input_sentence, spans_position,
                                           max_tokens, top_n, top_p, temperature,
                                           frequency_penalty, presence_penalty, 0)

        logits = self.decode_step(ctx, [self.tokenizer.sod_id])[0]
        decoder_ipts = self.tokenizer.get_span(0)
        blanks = [[]]
        next_span = 1

        for _ in range(max_tokens):
            logits = self.decode_step(ctx, [decoder_ipts])[0]
            decoder_ipts = sampler.sample(logits)
            if decoder_ipts == self.tokenizer.get_span(next_span):
                next_span += 1
                if next_span > len(spans_position):
                    break
                blanks.append([])
            else:
                blanks[-1].append(decoder_ipts)
        
        return [
            {
                "position": blank_pos,
                "text": self.id_to_text(blank_tokens)
            } 
            for blank_pos, blank_tokens in zip( spans_position, blanks )
        ]


    def generate(self, 
            input_sentence : str,
            max_tokens : int = 128,
            top_n : Optional[int] = None,
            top_p : Optional[float] = None,
            temperature : float = 0.9,
            frequency_penalty : float = 0,
            presence_penalty : float = 0,
            stop_tokens : Optional[List[str]] = None,
        ) -> Tuple[str, bool]:
        """Generate some words from the model.

        Args:
            input_sentence: Your input.
            max_tokens: Maximum number of tokens to generate.
            top_n: Only sampling from top n tokens in the result.
            top_p: Only sampling from tokens that comprising the top p probability in the result.
            temperature: Temperature for sampling. Higher values mean more diverse results. 
            frequency_penalty: A penalty used to avoid models generating the same content.
            presence_penalty: A penalty used to avoid models generating the same topic.
            stop_tokens: A list of tokens that will stop the generation.
        
        Returns:
            The result sentence and a boolean indicating whether stop_tokens has been generated.
        """
        # Input: ... <s_189>
        # Output: <s> <s_189> ...

        if stop_tokens is None:
            stop_tokens = []
        else:
            stop_tokens = [self.tokenizer.encode(i) for i in stop_tokens]

        # <eod> must be in the set of stop words.
        if not self.tokenizer.eod_id in stop_tokens:
            stop_tokens.append(self.tokenizer.eod_id)

        ctx, sampler, _ = self.pre_processing(
            input_sentence + SPAN_TOKEN, 
            [len(input_sentence)],
            max_tokens, top_n, top_p, temperature,
            frequency_penalty, presence_penalty, 189
        )


        logits = self.decode_step(ctx, [self.tokenizer.sod_id])[0]
        decoder_ipts = self.tokenizer.get_span(189)
        blanks = []

        stoped = False
        for _ in range(max_tokens):
            logits = self.decode_step(ctx, [decoder_ipts])[0]
            decoder_ipts = sampler.sample(logits)
            if decoder_ipts in stop_tokens:
                stoped = True
                break
            blanks.append(decoder_ipts)

        return self.id_to_text(blanks), stoped