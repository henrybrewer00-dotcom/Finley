"""Every template must compile. Catches a template that is missing from the package or broken."""

from pathlib import Path

from finley.web.deps import TEMPLATES_DIR, templates


def test_every_template_compiles():
    names = sorted(str(p.relative_to(TEMPLATES_DIR)) for p in Path(TEMPLATES_DIR).rglob("*.html"))
    assert len(names) > 10
    for name in names:
        templates.env.get_template(name)


def test_templates_ship_with_the_package():
    import tomllib

    data = tomllib.loads((Path(TEMPLATES_DIR).parents[1] / "pyproject.toml").read_text())
    patterns = data["tool"]["setuptools"]["package-data"]["finley"]
    for p in Path(TEMPLATES_DIR).rglob("*.html"):
        rel = p.relative_to(Path(TEMPLATES_DIR).parent)
        assert any(rel.match(pat) for pat in patterns), f"{rel} is not covered by package-data"
    assert not (Path(TEMPLATES_DIR) / "public").exists(), "Vercel won't bundle a folder named public"
