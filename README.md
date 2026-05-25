# SkillTrojan: Backdoor Attacks on Agent Skills

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

Official codebase for the ICML 2026 paper **"SkillTrojan: Backdoor Attacks on Skill-Based Agent Systems"**.

> ⚠️ **Responsible Research Only**
>
> This repository is for security research, red-team benchmarking, and defense studies. Do not deploy or evaluate attack pipelines on systems without explicit authorization.

---

## Overview

SkillTrojan studies a new backdoor attack surface in **skill-based LLM agents**. Instead of attacking model weights, the attack implants a hidden payload into the skill pipeline, then reconstructs and executes it only when a trigger condition is met.

### Key Characteristics

- **Encrypted Payload Fragmentation**: payload is encrypted and split into multiple fragments.
- **Trigger-Gated Activation**: attack only activates when trigger phrases appear.
- **Stealth Through Normal Workflow**: fragments are hidden in benign-looking tool outputs.
- **Modular Attack Configuration**: target payload, trigger, crypto, and fragment count are configurable.

---

## Repository Structure

```text
.
├── agent/                          # Agent runtime and memory/context management
├── attack/                         # Payload targets, crypto utils, and backdoor skill generator
├── config/                         # Clean and attack experiment configs
├── skills/
│   ├── ehr_sql/                    # Clean skill
│   ├── enhanced-ehr-sql/           # Enhanced clean skill
│   └── backdoored-ehr-sql/         # Backdoored skill example
├── tools/                          # Tool implementations used by agent
├── utils/                          # Shared utilities
├── ehr_run.py                      # Clean evaluation script
├── ehr_run_attack.py               # Attack evaluation script
└── run.py / run_models.py          # Auxiliary run entrypoints
```

---

## Installation

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## Configuration

### Clean Evaluation Config

`config/default.yaml`:

- `api_url`: model endpoint base URL
- `api_key`: API credential (**keep local; never commit real keys**)
- `model_name`: model identifier
- `skill_path`: clean skill path

### Attack Evaluation Config

`config/attack.yaml`:

- `attack_config.trigger`: trigger phrase
- `attack_config.num_fragments`: payload fragment count
- `attack_config.encryption.encryption_method`: encryption method
- `attack_config.encryption.encryption_key`: local experimental key

---

## Quick Start

### 1) Run clean pipeline

```bash
python ehr_run.py --config config/default.yaml
```

### 2) Generate backdoored skills

```bash
python -m attack.generate_backdoor_skills --list-targets

python -m attack.generate_backdoor_skills \
  --target system_info \
  --trigger "enable advanced mode" \
  --num-fragments 3 \
  --crypto-method base64_xor
```

### 3) Run attack evaluation

```bash
python ehr_run_attack.py --config config/attack.yaml
```

---

## Attack Pipeline (High-Level)

1. Define payload target in `attack/target.py`.
2. Encrypt payload and split into fragments (`attack/crypto_utils.py`).
3. Inject fragments into multiple skill tool outputs.
4. Trigger enhanced workflow with trigger phrase.
5. Reassemble/decrypt/execute payload in verification stage.

---

## Release Hygiene (Security/Privacy)

Before public release:

- Keep all credentials as placeholders in committed configs.
- Remove local logs/outputs that may contain private prompts or data.
- Check for accidental secret leakage:

```bash
rg -n "(api[_-]?key|secret|token|password|AKIA|ghp_|sk-[A-Za-z0-9]{20,}|BEGIN RSA|PRIVATE KEY)" -g '!*.pyc'
```

---

## License

This project is licensed under the **Apache License 2.0**. See [LICENSE](LICENSE) for details.

---

## Citation

If you find this repository useful, please cite:

```bibtex
@inproceedings{feng2026skilltrojan,
  title={SkillTrojan: Backdoor Attacks on Skill-Based Agent Systems},
  author={Yunhao Feng and Yifan Ding and Yingshui Tan and Boren Zheng and Yanming Guo and Xiaolong Li and Kun Zhai and Yishan Li and Wenke Huang},
  booktitle={Proceedings of the 43rd International Conference on Machine Learning (ICML)},
  year={2026},
  note={arXiv preprint arXiv:2604.06811},
  url={https://arxiv.org/abs/2604.06811}
}
```
