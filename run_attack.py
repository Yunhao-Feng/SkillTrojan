from utils import load_config
from attack import SkillTrojan



if __name__ == "__main__":
    import torch.multiprocessing as mp
    mp.set_start_method('spawn', force=True)

    config = load_config("config/default.yaml")
    skill_trojan = SkillTrojan(config)
    skill_trojan.run()