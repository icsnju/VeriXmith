class Circuit:

    FILENAME_EXTENSION = None

    def __init__(self, data, model):
        self.data = data
        self.model = model

    def is_equivalent_to(self, *others, **kwargs) -> bool:
        raise NotImplementedError(f'equivalence check of {self.__class__.__name__} is not supported')

    def to_file(self, filepath: str) -> None:
        raise NotImplementedError(f'saving {self.__class__.__name__} to file is not supported')

    @classmethod
    def from_file(cls, filepath: str):
        raise NotImplementedError(f'constructing {cls.__name__} from a file is not supported')
