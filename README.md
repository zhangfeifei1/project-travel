<h1 align="center">BMInf</h1>
<p align="center">
  <a href="https://bminf.readthedocs.io/" target="_blank">Documentation</a> • <a href="https://github.com/OpenBMB/BMInf-demos">Demo</a> •  <a href="#features">Features</a> • <a href="#install">Installation</a> • <a href="#quick-start">Quick Start</a> • <a href="#supported-models">Supported Models</a> • <a href="./README-ZH.md" target="_blank">简体中文</a>
<br>
</p>

BMInf (Big Model Inference) is a low-resource inference package for large-scale pretrained language models (PLMs). It has following features:
<div id="features"></div>

- **Hardware Friendly.** BMInf supports running models with more than 10 billion parameters on a single NVIDIA GTX 1060 GPU in its minimum requirements. Running with better GPUs leads to better performance. In cases where the GPU memory supports the large model inference (such as V100 or A100), BMInf still has a significant performance improvement over the existing PyTorch implementation.
- **Open.** The parameters of models are open. Users can access large models locally with their own machines without applying or accessing an online API.  
- **Comprehensive Ability.**  BMInf supports generative model CPM1 [[1](#ref)], general language model CPM2.1 [[2](#ref)], and dialogue model EVA [[3](#ref)]. The abilities of these models cover text completion, text generation, and dialogue generation.
- **Upgraded Model.** Based on CPM2 [[2](#ref)], the newly upgraded model CPM2.1 is currently supported. Based on continual learning, the text generation ability of CPM2.1 is greatly improved compared to CPM2.
- **Convenient Deployment.** Using BMInf, it will be fast and convenient to develop interesting downstream applications.


## Demo
![demo](./docs/source/images/demo.gif)

## Documentation
Our [documentation](https://bminf.readthedocs.io/) provides more information about the package.

<div id="install"></div>

## Install

- From pip: ``pip install bminf``

- From source code: download the package and run ``python setup.py install``

- From docker: ``docker run -it --gpus 1 -v $HOME/.cache/bigmodels:/root/.cache/bigmodels --rm openbmb/bminf python3 examples/fill_blank.py``

### Hardware Requirement

Here we list the minimum and recommended configurations for running BMInf. 

| | Minimum Configuration | Recommended Configuration |
|-|-|-|
| Memory | 16GB | 24GB
| GPU | NVIDIA GeForce GTX 1060 6GB | NVIDIA Tesla V100 16GB
| PCI-E |  PCI-E 3.0 x16 |  PCI-E 3.0 x16

GPUs with the Pascal micro-architecture or newer architecture are supported by BMInf. Refer to the [table](https://en.wikipedia.org/wiki/CUDA#GPUs_supported) to check whether your GPU is supported. 

### Software Requirement

BMInf requires CUDA version >= 10.1 and all the dependencies can be automaticlly installed by the installation process.

- **python** >= 3.6
- **requests**
- **tqdm** 
- **jieba**
- **numpy** 
- **cupy-cuda<your_cuda_version>** >= 9, <10

<div id="quick-start"></div>

## Quick Start

Here we provide a simple script for using BMInf. 

Firstly, import a model from the model base (e.g. CPM1, CPM2, EVA).
```python
import bminf
cpm2 = bminf.models.CPM2()
```

Then define the text and use the ``<span>`` token to denote the blank to fill in.
```python
text = "北京环球度假区相关负责人介绍，北京环球影城指定单日门票将采用<span>制度，即推出淡季日、平季日、旺季日和特定日门票。<span>价格为418元，<span>价格为528元，<span>价格为638元，<span>价格为<span>元。北京环球度假区将提供90天滚动价格日历，以方便游客提前规划行程。"
```

Use the ``fill_blank`` function to obtain the results and replace ``<span>`` tokens with the results.

```python
for result in cpm2.fill_blank(text, 
    top_p=1.0,
    top_n=10, 
    temperature=0.9,
    frequency_penalty=0,
    presence_penalty=0
):
    value = result["text"]
    text = text.replace("<span>", "\033[0;32m" + value + "\033[0m", 1)
print(text)
```
Finally, you can get the predicted text. For more examples, go to the ``examples`` folder.

<div id="supported-models"></div>

## Supported Models

BMInf currently supports these models:

- **CPM2.1.** CPM2.1 is an upgraded version of CPM2 [[1](#ref)], which is a general Chinese pre-trained language model with 11 billion parameters. Based on CPM2, CPM2.1 introduces a generative pre-training task and was trained via the continual learning paradigm. In experiments, CPM2.1 has a better generation ability than CPM2.

- **CPM1.** CPM1 [[2](#ref)] is a generative Chinese pre-trained language model with 2.6 billion parameters. The architecture of CPM1 is similar to GPT [[4](#ref)] and it can be used in various NLP tasks such as conversation, essay generation, cloze test, and language understanding.

- **EVA.** EVA [[3](#ref)] is a Chinese pre-trained dialogue model with 2.8 billion parameters. EVA performs well on many dialogue tasks, especially in the multi-turn interaction of human-bot conversations.

Besides these models, we are now working on adding more PLMs especially large-scale PLMs. We welcome every contributor to add their models to this project by proposing an issue.

## Performances

Here we report the speeds of CPM2 encoder and decoder we have tested on different platforms. You can also run ``benchmark/cpm2/encoder.py`` and ``benchmark/cpm2/decoder.py`` to test the speed on your machine!

Implementation | GPU | Encoder Speed (tokens/s) | Decoder Speed (tokens/s) |
|-|-|-|-|
BMInf | NVIDIA GeForce GTX 1060 | 533 | 1.6
BMInf | NVIDIA GeForce GTX 1080Ti | 1200 | 12
BMInf | NVIDIA GeForce GTX 2080Ti | 2275 | 19
BMInf | NVIDIA Tesla V100 | 2966 | 20
BMInf | NVIDIA Tesla A100 | 4365 | 26
PyTorch | NVIDIA Tesla V100 | - | 3
PyTorch | NVIDIA Tesla A100 | - | 7

## Contributing
Here is the QRCode to our WeChat user community and we welcome others to contribute codes following our [contributing guidelines](https://github.com/OpenBMB/BMInf/blob/master/CONTRIBUTING.md).

![Our community](./docs/source/images/community.jpeg)

## License

The package is released under the [Apache 2.0](https://github.com/OpenBMB/BMInf/blob/master/LICENSE) License.

## References
<div id="ref"></div>

1. [CPM-2: Large-scale Cost-efficient Pre-trained Language Models.](https://arxiv.org/abs/2106.10715) Zhengyan Zhang, Yuxian Gu, Xu Han, Shengqi Chen, Chaojun Xiao, Zhenbo Sun, Yuan Yao, Fanchao Qi, Jian Guan, Pei Ke, Yanzheng Cai, Guoyang Zeng, Zhixing Tan, Zhiyuan Liu, Minlie Huang, Wentao Han, Yang Liu, Xiaoyan Zhu, Maosong Sun.
2. [CPM: A Large-scale Generative Chinese Pre-trained Language Model.](https://arxiv.org/abs/2012.00413) Zhengyan Zhang, Xu Han, Hao Zhou, Pei Ke, Yuxian Gu, Deming Ye, Yujia Qin, Yusheng Su, Haozhe Ji, Jian Guan, Fanchao Qi, Xiaozhi Wang, Yanan Zheng, Guoyang Zeng, Huanqi Cao, Shengqi Chen, Daixuan Li, Zhenbo Sun, Zhiyuan Liu, Minlie Huang, Wentao Han, Jie Tang, Juanzi Li, Xiaoyan Zhu, Maosong Sun.
3. [EVA: An Open-Domain Chinese Dialogue System with Large-Scale Generative Pre-Training.](https://arxiv.org/abs/2108.01547) Hao Zhou, Pei Ke, Zheng Zhang, Yuxian Gu, Yinhe Zheng, Chujie Zheng, Yida Wang, Chen Henry Wu, Hao Sun, Xiaocong Yang, Bosi Wen, Xiaoyan Zhu, Minlie Huang, Jie Tang.
4. [Language Models are Unsupervised Multitask Learners.](http://www.persagen.com/files/misc/radford2019language.pdf) Alec Radford, Jeffrey Wu, Rewon Child, David Luan, Dario Amodei, and Ilya Sutskever.
