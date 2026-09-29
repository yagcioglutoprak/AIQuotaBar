"""Auto-update against real throwaway git repos (no network)."""

import shutil
import subprocess

import pytest

from aiquotabar.update import _check_and_apply_update

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


def commit(repo, name):
    (repo / name).write_text(name)
    git(repo, "add", name)
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", name)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repos(tmp_path):
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    git(upstream, "init", "-q", "-b", "main")
    commit(upstream, "a")
    install = tmp_path / "install"
    git(tmp_path, "clone", "-q", str(upstream), str(install))
    return upstream, install


def test_up_to_date_is_not_an_update(repos):
    _, install = repos
    assert _check_and_apply_update(str(install)) is False


def test_new_upstream_commit_is_applied(repos):
    upstream, install = repos
    new = commit(upstream, "b")
    assert _check_and_apply_update(str(install)) is True
    assert git(install, "rev-parse", "HEAD") == new


def test_local_commits_ahead_of_upstream_do_not_restart(repos):
    _, install = repos
    ahead = commit(install, "local")
    assert _check_and_apply_update(str(install)) is False
    assert git(install, "rev-parse", "HEAD") == ahead
    assert git(install, "stash", "list") == ""


def test_diverged_copy_is_left_alone(repos):
    upstream, install = repos
    commit(upstream, "b")
    mine = commit(install, "local")
    assert _check_and_apply_update(str(install)) is False
    assert git(install, "rev-parse", "HEAD") == mine


def test_not_a_git_install(tmp_path):
    assert _check_and_apply_update(str(tmp_path)) is False
