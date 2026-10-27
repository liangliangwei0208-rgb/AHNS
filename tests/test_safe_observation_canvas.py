"""观察图固定像素尺寸：只用离线图片，不拉行情、不发送邮件。"""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image, ImageChops, ImageDraw

from tools import safe_display


TARGET = (2329, 3939)
FILENAMES = (
    "safe_haiwai_premarket.png",
    "safe_haiwai_afterhours.png",
    "safe_haiwai_fund.png",
    "safe_haiwai_intraday.png",
    "safe_haiwai_night.png",
)


@pytest.fixture
def image_directory():
    # 保留测试产物，避免测试代码主动批量删除文件或目录。
    return Path(tempfile.mkdtemp(prefix="ahns-observation-canvas-"))


def content_image(size, bottom=3700):
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((29, 95, min(size[0] - 29, 2300), bottom), outline="black", width=2)
    draw.line((40, 110, min(size[0] - 40, 2280), bottom - 10), fill="#bd1234", width=3)
    return image


@pytest.mark.parametrize("height,bottom", [(4011, 3763), (4084, 3836), (3939, 3691)])
def test_trimming_blank_bottom_preserves_every_retained_pixel(image_directory, height, bottom):
    path = image_directory / "safe_haiwai_fund.png"
    original = content_image((2329, height), bottom)
    original.save(path)

    safe_display.normalize_safe_observation_canvas(path)

    with Image.open(path) as result:
        assert result.size == TARGET
        assert result.format == "PNG"
        assert ImageChops.difference(result.convert("RGB"), original.crop((0, 0, *TARGET))).getbbox() is None


@pytest.mark.parametrize("filename", FILENAMES)
def test_all_five_normalize_before_any_watermark(image_directory, filename):
    path = image_directory / filename
    content_image((2329, 4011)).save(path)
    calls = []

    def check_size(stage):
        def check(output_file, **kwargs):
            with Image.open(output_file) as image:
                assert image.size == TARGET
            calls.append(stage)
        return check

    with patch.object(safe_display, "add_center_image_watermark", side_effect=check_size("logo")), \
         patch.object(safe_display, "add_brand_text_watermark", side_effect=check_size("brand")), \
         patch.object(safe_display, "add_scene_text_watermark", side_effect=check_size("scene")):
        safe_display.apply_safe_public_watermarks(path)
    assert calls == ["logo", "brand", "scene"]


@pytest.mark.parametrize("filename", ["safe_holidays.png", "safe_sum_holidays.png", "1_三季度_012922.png"])
def test_other_images_are_not_reencoded_or_resized(image_directory, filename):
    path = image_directory / filename
    Image.new("RGB", (3062, 4386), "#fafafa").save(path)
    before = path.read_bytes()

    safe_display.normalize_safe_observation_canvas(path)

    assert path.read_bytes() == before


def test_small_image_is_padded_without_enlarging_content(image_directory):
    path = image_directory / "safe_haiwai_premarket.png"
    original = content_image((500, 700), 600)
    original.save(path)

    safe_display.normalize_safe_observation_canvas(path)

    with Image.open(path) as result:
        assert result.size == TARGET
        x, y = (TARGET[0] - 500) // 2, (TARGET[1] - 700) // 2
        retained = result.crop((x, y, x + 500, y + 700)).convert("RGB")
        assert ImageChops.difference(retained, original).getbbox() is None


@pytest.mark.parametrize("size", [(2329, 6000), (5000, 3939), (5000, 6000)])
def test_overflow_keeps_all_corners_with_proportional_scale_and_margin(image_directory, size):
    path = image_directory / "safe_haiwai_intraday.png"
    original = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(original)
    bounds = (29, 80, size[0] - 29, size[1] - 90)
    draw.rectangle(bounds, fill="#dddddd")
    left, top, right, bottom = bounds
    for color, x, y in [("red", left, top), ("green", right - 100, top),
                         ("blue", left, bottom - 100), ("black", right - 100, bottom - 100)]:
        draw.rectangle((x, y, x + 100, y + 100), fill=color)
    original.save(path)

    safe_display.normalize_safe_observation_canvas(path)

    with Image.open(path) as result:
        result = result.convert("RGB")
        assert result.size == TARGET
        bbox = ImageChops.difference(result, Image.new("RGB", TARGET, "white")).getbbox()
        assert bbox is not None
        l, t, r, b = bbox
        assert min(l, t, TARGET[0] - r, TARGET[1] - b) >= 24
        assert abs((l + r) - TARGET[0]) <= 1
        assert abs((t + b) - TARGET[1]) <= 1
        expected_ratio = (right - left + 1) / (bottom - top + 1)
        assert abs((r - l) / (b - t) - expected_ratio) < 0.002
        # 四角标记均留在对应象限，证明末行/长备注没有被裁掉。
        centers = [("red", 0.03, 0.03), ("green", 0.97, 0.03),
                   ("blue", 0.03, 0.97), ("black", 0.97, 0.97)]
        from PIL import ImageColor
        for color, xr, yr in centers:
            # 标记在较长图里只占很小区域，直接检查每个象限含有该原色。
            quadrant = result.crop((l if xr < 0.5 else (l + r) // 2,
                                    t if yr < 0.5 else (t + b) // 2,
                                    (l + r) // 2 if xr < 0.5 else r,
                                    (t + b) // 2 if yr < 0.5 else b))
            assert ImageColor.getrgb(color) in {rgb for count, rgb in quadrant.getcolors(quadrant.width * quadrant.height)}


def test_very_light_bottom_content_is_not_mistaken_for_blank(image_directory):
    path = image_directory / "safe_haiwai_afterhours.png"
    original = Image.new("RGB", (2329, 4084), "white")
    ImageDraw.Draw(original).rectangle((50, 4050, 300, 4070), fill=(254, 254, 254))
    original.save(path)

    safe_display.normalize_safe_observation_canvas(path)

    with Image.open(path) as result:
        assert result.size == TARGET
        assert ImageChops.difference(result.convert("RGB"), Image.new("RGB", TARGET, "white")).getbbox() is not None


def test_transparency_is_composited_on_white(image_directory):
    path = image_directory / "safe_haiwai_premarket.png"
    original = Image.new("RGBA", (500, 700), (0, 0, 0, 0))
    ImageDraw.Draw(original).rectangle((40, 50, 460, 650), fill=(255, 0, 0, 255))
    original.save(path)

    safe_display.normalize_safe_observation_canvas(path)

    with Image.open(path) as result:
        assert result.size == TARGET
        assert result.convert("RGB").getpixel((0, 0)) == (255, 255, 255)
        assert result.convert("RGB").getpixel((TARGET[0] // 2, TARGET[1] // 2)) == (255, 0, 0)


def test_blank_image_is_a_white_fixed_canvas(image_directory):
    path = image_directory / "safe_haiwai_night.png"
    Image.new("RGB", (3000, 5000), "white").save(path)

    safe_display.normalize_safe_observation_canvas(path)

    with Image.open(path) as result:
        assert result.size == TARGET
        assert ImageChops.difference(result.convert("RGB"), Image.new("RGB", TARGET, "white")).getbbox() is None
