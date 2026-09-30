"""为本轮邮件生成短名；不复制、不移动、不重命名任何图片。"""
import re
from pathlib import Path
from tools.configs.email_image_name_configs import EMAIL_IMAGE_NAMES, EMAIL_STEP_NAMES


def build_image_names(image_paths, step_names=None):
    """顺序与image_paths一一对应，动态基金保留代码，重复名称自动加序号。"""
    step_names = step_names or {}
    names, used = [], set()
    for index, raw in enumerate(image_paths, 1):
        path = Path(raw)
        base = EMAIL_IMAGE_NAMES.get(path.name)
        parts = set(path.parts)
        if path.stem.startswith("equity_bond_spread_"):
            base = "股债利差"
        elif "fund_holding_change" in parts or "fund_limit_change" in parts:
            code = re.search(r"(?:^|_)(\d{6})(?:_|$)", path.stem)
            if code:
                base = ("持仓" if "fund_holding_change" in parts else "限购") + "_" + code.group(1)
        elif "fund_region_allocation" in parts:
            if "manual" in parts and re.match(r"\d{6}", path.stem):
                base = "地区分布_" + path.stem[:6]
            else:
                page = re.match(r"(\d+)_", path.stem)
                base = "地区分布_" + (page.group(1) if page else str(index))
        if base is None:
            step = str(step_names.get(path.resolve(), "其他图"))
            prefix = EMAIL_STEP_NAMES.get(step, re.sub(r'[\\/:*?"<>|\s]', '', step)[:10] or "其他图")
            base = f"{prefix}_{index:02d}"
        suffix = path.suffix.lower()
        candidate, ordinal = base+suffix, 1
        while candidate.casefold() in used:
            ordinal += 1
            candidate = f"{base}_{ordinal}{suffix}"
        used.add(candidate.casefold())
        names.append(candidate)
    return names
