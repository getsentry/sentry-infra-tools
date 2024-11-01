from unittest.mock import Mock, patch
import pytest
from libsentrykube.git import Git, RepoNotCleanException


@pytest.fixture
def mock_repo():
    with patch("git.Repo") as mock_repo:
        # Create mock heads
        mock_main = Mock(name="main")
        mock_develop = Mock(name="develop")

        # Set up name attributes for the heads
        mock_main.name = "main"
        mock_main.checkout = Mock()
        mock_develop.name = "develop"
        mock_develop.checkout = Mock()

        # Set up the heads list
        mock_repo.return_value.heads = [mock_main, mock_develop]

        # Other mock setup
        mock_repo.return_value.active_branch.name = "develop"
        mock_repo.return_value.is_dirty.return_value = False
        mock_repo.return_value.git = Mock()
        mock_repo.return_value.main.c
        yield mock_repo.return_value


def test_default_branch(mock_repo):
    mock_repo.return_value.heads = ["main", "develop"]
    git_instance = Git("a/b/c")
    assert git_instance.default_branch == "main"


def test_switch_to_default_branch_already_on_default(mock_repo):
    git_instance = Git("a/b/c")
    mock_repo.active_branch.name = "main"
    git_instance.switch_to_default_branch(force=False)
    # Should not call checkout since we're already on main
    mock_repo.heads[0].checkout.assert_not_called()


def test_switch_to_default_branch_clean_repo(mock_repo):
    git_instance = Git("a/b/c")
    mock_repo.active_branch.name = "develop"
    mock_repo.is_dirty.return_value = False
    git_instance.switch_to_default_branch(force=False)
    mock_repo.heads[0].checkout.assert_called_once()


def test_switch_to_default_branch_dirty_repo_no_force(mock_repo):
    git_instance = Git("a/b/c")
    mock_repo.active_branch.name = "develop"
    mock_repo.is_dirty.return_value = True

    with pytest.raises(RepoNotCleanException):
        git_instance.switch_to_default_branch(force=False)


def test_switch_to_default_branch_dirty_repo_force(mock_repo):
    git_instance = Git("a/b/c")
    mock_repo.active_branch.name = "develop"
    mock_repo.is_dirty.return_value = True

    git_instance.switch_to_default_branch(force=True)
    mock_repo.git.stash.assert_called_once()
    mock_repo.heads[0].checkout.assert_called_once()
    assert git_instance.stashed is True


def test_pop_stash_when_stashed(mock_repo):
    git_instance = Git("a/b/c")
    git_instance.stashed = True
    git_instance.pop_stash()
    mock_repo.git.stash.assert_called_once_with("pop")
    assert git_instance.stashed is False
