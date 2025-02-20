"""Define the DictList class."""


class DictList(list):
    """Define a combined dict and list.

    This object behaves like a list, but has the O(1) speed
    benefits of a dict when looking up elements by their id.

    """

    def __init__(self, iterable: list) -> None:
        """Instance of DictList.

        Input
        -----
        iterable : list
            A list containing the objects needed in the DictList
        """
        list.extend(self, iterable)
        self._dict = self._generate_index()

    def _generate_index(self) -> dict:
        return {obj.id: k for k, obj in enumerate(self)}

    def get_by_id(self, identifier: str) -> None:
        """Find a member of DictList by the id."""
        return list.__getitem__(self, self._dict[identifier])
