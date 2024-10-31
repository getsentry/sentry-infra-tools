import git


class RepoNotCleanException(Exception):
    pass


class Git:
    def __init__(self):
        self.repo = git.Repo()
        if "main" in self.repo.heads:
            self.default_branch = "main"
        else:
            self.default_branch = "master"

    def switch_to_default_branch(self, *, force: bool) -> None:
        if self.repo.active_branch.name == self.default_branch:
            return

        # check if there are any changes
        if self.repo.is_dirty():
            if not force:
                print("Repo not clean. Cannot switch to default branch")
                raise RepoNotCleanException

            print("Stashing current work since force is true")
            self.repo.git.stash()
            self.stashed = True

        print(f"Checking out {self.default_branch}")
        print(self.repo.heads)
        # Find the branch by name and checkout
        default_branch = next(
            head for head in self.repo.heads if head.name == self.default_branch
        )
        default_branch.checkout()
        print(f"Active branch: {self.repo.active_branch.name}")
        print(f"Default branch: {self.default_branch}")
        assert self.repo.active_branch.name == self.default_branch

    def stash(self) -> None:
        self.repo.git.stash()
        self.stashed = True

    def pop_stash(self) -> None:
        if not self.stashed:
            return

        self.repo.git.stash("pop")
        self.stashed = False

    def pull_latest_changes(self) -> None:
        self.repo.git.pull()

    def add(self, files: list[str]) -> None:
        self.repo.index.add(files)

    def commit(self, message: str) -> None:
        self.repo.index.commit(message)

    def create_branch(self, branch_name: str) -> None:
        self.repo.create_head(branch_name, commit=self.default_branch)


def go_to_main() -> None:
    """
    Switches to main stashes the current work.
    TODO: Add arguments
    """
    raise NotImplementedError


def pull_main() -> None:
    """
    Pull main
    TODO: Add arguments
    """
    raise NotImplementedError


def create_branch() -> None:
    """
    TODO: Add arguments
    """
    raise NotImplementedError
