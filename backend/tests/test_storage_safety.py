import pytest
from utils import storage


@pytest.fixture
def temp_projects_dir(tmp_path, monkeypatch):
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(storage, "PROJECTS_DIR", projects_dir)
    return projects_dir


@pytest.mark.parametrize("bad_name", [
    "",
    " padded ",
    "..",
    ".",
    "a/b",
    "a\\b",
    "CON",
    "con.txt",
    "NUL",
    "COM1",
    "LPT1",
    "a:b",
    "a<b",
    "a>b",
    'a"b',
    "a|b",
    "a?b",
    "a*b",
])
def test_safe_name_rejects_dangerous_names(bad_name):
    with pytest.raises(ValueError):
        storage.safe_name(bad_name)


@pytest.mark.parametrize("good_name", [
    "My Show",
    "S01E01",
    "Ελληνικός Τίτλος",  # Greek
    "アニメ",  # Japanese
    "Show (2024)",
    "Show - Season 1",
])
def test_safe_name_accepts_normal_names(good_name):
    assert storage.safe_name(good_name) == good_name


def test_project_dir_rejects_traversal(temp_projects_dir):
    with pytest.raises(ValueError):
        storage.project_dir("..")
    with pytest.raises(ValueError):
        storage.project_dir("../../etc")
    with pytest.raises(ValueError):
        storage.project_dir("a/../../b")


def test_project_dir_stays_inside_root(temp_projects_dir):
    p = storage.project_dir("My Show")
    assert p == (temp_projects_dir / "My Show").resolve()
    assert p.is_relative_to(temp_projects_dir.resolve())


def test_project_dir_accepts_unicode_names(temp_projects_dir):
    p = storage.project_dir("Ελληνικός Τίτλος")
    assert p.is_relative_to(temp_projects_dir.resolve())


def test_episode_dir_rejects_traversal(temp_projects_dir):
    with pytest.raises(ValueError):
        storage.episode_dir("My Show", "..")
    with pytest.raises(ValueError):
        storage.episode_dir("My Show", "../../../etc/passwd")


def test_episode_dir_stays_inside_root(temp_projects_dir):
    p = storage.episode_dir("My Show", "S01E01")
    assert p == (temp_projects_dir / "My Show" / "episodes" / "S01E01").resolve()
    assert p.is_relative_to(temp_projects_dir.resolve())


def test_create_project_rejects_unsafe_name(temp_projects_dir):
    with pytest.raises(ValueError):
        storage.create_project("../evil")
