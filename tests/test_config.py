"""Area configs: an area file extends the base and only overrides what differs."""

from pathlib import Path

from flatcast.config import area_configs, load_config


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_area_extends_base_and_resolves_paths_from_project(tmp_path):
    write(
        tmp_path / "config.yaml",
        "project: {data_dir: data, output_dir: outputs, site_dir: site, flats_file: config/flats.geojson}\n"
        "areas: [config.yaml, config/areas/b.yaml]\n"
        "area: {id: a, name: Alpha, bbox: [0, 0, 1, 1], resolution_m: 30}\n"
        "imagery: {mndwi: {water_above: 0.1, dry_below: -0.1}}\n",
    )
    write(
        tmp_path / "config/areas/b.yaml",
        "extends: ../../config.yaml\n"
        "project: {output_dir: outputs/b}\n"
        "area: {id: b, name: Beta, bbox: [2, 2, 3, 3]}\n"
        "imagery: {mndwi: {dry_below: -0.2}}\n",
    )
    a, b = area_configs(load_config(tmp_path / "config.yaml"))
    assert (a.area_id, b.area_id) == ("a", "b")
    assert b.area.resolution_m == 30  # inherited
    assert b.imagery.mndwi.water_above == 0.1 and b.imagery.mndwi.dry_below == -0.2  # deep merge
    assert b.paths.outputs == tmp_path / "outputs" / "b"  # relative to the project, not the area file
    assert b.paths.site == a.paths.site  # one site for every area
    assert "areas" not in b


def test_single_config_without_areas_is_its_own_area(tmp_path):
    cfg = load_config(write(tmp_path / "config.yaml", "area: {bbox: [0, 0, 1, 1]}\n"))
    assert area_configs(cfg) == [cfg]
    assert cfg.area_id == "main"
