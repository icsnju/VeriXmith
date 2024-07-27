import tempfile
import warnings
from datetime import datetime
from pathlib import Path
from shutil import copytree, ignore_patterns

from invoke.config import Config
from invoke.context import Context


class Workspace:
    """A valid Workspace object contains these items:
    1. Path to the temporary directory. It is used to hold intermediate files during the validation of a test case.
    2. The `Context` object in invoke.

    Workspaces are created to maintain temporary files during a certain interval of process.
    This class must be used as a context manager. For example::

        with Workspace() as workspace:
            ...

    The temporary files will be removed after exiting the context."""

    result_dir = Path(tempfile.gettempdir())

    def __enter__(self):

        # Create the temporary directory for current worker thread
        self._tmpdir = tempfile.TemporaryDirectory(dir=self.__class__.result_dir)

        # Create the Context object used by invoke
        # NOTE: disable output of invoke tasks
        self.context = Context(Config(overrides={'run': {'hide': True}}))

        # NOTE: suppress the ResourceWarnings caused by "invoke", FutureWarnings caused by "tree-sitter"
        warnings.filterwarnings(action='ignore', category=ResourceWarning)
        warnings.filterwarnings(action='ignore', category=FutureWarning)

        push_workspace(self)
        return self

    def __exit__(self, exc, value, tb):
        # Clean up the temporary directory
        self._tmpdir.cleanup()

        # Remove itself from WORKSPACES_STACK
        pop_workspace()

    @property
    def tmpdir(self) -> Path:
        return Path(self._tmpdir.name)

    def _fresh_name(self):
        """Generate a unique name for a test case causing crash / difference."""
        return f'{datetime.today().strftime("%Y%m%d_%H%M%S_%f")}_{self.tmpdir.name}'

    def save_as(self, label: str) -> Path:
        """Copy all files except klee*/ in current directory to `{label}/`."""

        return copytree(src=self.tmpdir,
                        dst=self.__class__.result_dir / label / self._fresh_name(),
                        ignore=ignore_patterns('test*', 'assembly.ll', 'run*stats'))

    def path_to_temp_dir(self, dirname: str, unique=True) -> Path:
        """Get the absolute path to a subdirectory `dirname` under the `tmpdir` of current environment.
        This method will not create the directory even if it doesn't exist yet."""

        dirpath = self.tmpdir / dirname

        if unique:
            suffix = 0
            while dirpath.exists():
                dirpath = dirpath.with_name(dirname + str(suffix))
                suffix += 1

        return dirpath

    def path_to_temp_file(self, filename: str, unique=True) -> Path:
        """Get the absolute path to the file `filename` under the `tmpdir` of current environment.
        If there has already been a directory at that place, raise `IsADirectoryError`."""

        filepath = self.tmpdir / filename

        if unique:
            stem = filepath.stem
            suffix = 0
            while filepath.exists():
                filepath = filepath.with_stem(stem + str(suffix))
                suffix += 1

        elif filepath.is_dir():  # If filepath is already unique, it cannot be a directory.
            raise IsADirectoryError(f'file name "{filename}" conflicts with an existing directory.')

        return filepath

    def save_to_file(self, content, filename, unique=True) -> Path:
        """Writes given `content` to a file named `filename` under the tmpdir.
        Returns the absolute path to the file."""

        if isinstance(content, bytes):
            content = content.decode()
        elif not isinstance(content, str):
            content = repr(content)

        filepath = self.path_to_temp_file(filename, unique)
        filepath.write_text(content)
        return filepath


WORKSPACES_STACK = []


def get_workspace() -> Workspace:
    """Returns the `Workspace` at the top of the stack."""
    return WORKSPACES_STACK[-1]


def push_workspace(workspace) -> None:
    """Push the given workspace onto the stack."""
    WORKSPACES_STACK.append(workspace)


def pop_workspace() -> Workspace:
    """Pop a workspace from the stack."""
    return WORKSPACES_STACK.pop()
