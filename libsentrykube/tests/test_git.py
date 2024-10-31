from unittest.mock import Mock, patch
import pytest
from libsentrykube.git import Git, RepoNotCleanException


@pytest.fixture
def mock_repo():
    with patch("git.Repo") as mock_repo:
        # Setup mock heads to support dictionary-style access
        mock_heads = Mock()
        mock_heads.__getitem__ = lambda self, key: Mock(name=f"head_{key}")
        mock_repo.return_value.heads = mock_heads

        # Other mock setup
        mock_repo.return_value.active_branch.name = "develop"
        mock_repo.return_value.is_dirty.return_value = False
        mock_repo.return_value.git = Mock()
        yield mock_repo.return_value


@pytest.mark.parametrize(
    "heads",
    [
        pytest.param(["main", "some_branch"], id="main"),
        pytest.param(["master", "some_other_branch"], id="master"),
    ],
)
def test_default_branch(mock_repo, heads):
    mock_repo.heads = heads
    git_instance = Git()
    assert git_instance.default_branch == heads[0]


def test_switch_to_default_branch_already_on_default(mock_repo):
    git_instance = Git()
    mock_repo.active_branch.name = "main"
    git_instance.switch_to_default_branch(force=False)
    # Should not call checkout since we're already on main
    mock_repo.git.checkout.assert_not_called()


def test_switch_to_default_branch_clean_repo(mock_repo):
    git_instance = Git()
    mock_repo.active_branch.name = "feature"
    mock_repo.is_dirty.return_value = False
    git_instance.switch_to_default_branch(force=False)
    mock_repo.git.checkout.assert_called_once_with("main")


def test_switch_to_default_branch_dirty_repo_no_force(mock_repo):
    git_instance = Git()
    mock_repo.active_branch.name = "feature"
    mock_repo.is_dirty.return_value = True

    with pytest.raises(RepoNotCleanException):
        git_instance.switch_to_default_branch(force=False)


def test_switch_to_default_branch_dirty_repo_force(mock_repo):
    git_instance = Git()
    mock_repo.active_branch.name = "feature"
    mock_repo.is_dirty.return_value = True

    git_instance.switch_to_default_branch(force=True)
    mock_repo.git.stash.assert_called_once()
    mock_repo.git.checkout.assert_called_once_with("main")
    assert git_instance.stashed is True


def test_pop_stash_when_stashed(mock_repo):
    git_instance = Git()
    git_instance.stashed = True
    git_instance.pop_stash()
    mock_repo.git.stash.assert_called_once_with("pop")
    assert git_instance.stashed is False
