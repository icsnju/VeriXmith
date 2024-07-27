import sys
from typing import TextIO

import networkx as nx


class WorldMap:
    """When a circuit or translator is created, it will be registered in this class."""

    __graph = nx.MultiDiGraph()

    @classmethod
    def draw(cls, translator):
        instance_count = translator.instance_count()
        for start, end in translator.edges:
            cls.__graph.add_edge(start,
                                 end,
                                 key=translator,
                                 translator=translator.__name__,
                                 instance_count=instance_count)

    @classmethod
    def travel(cls, start: type, end: type):
        """Returns all the paths from start to end."""

        sinks = (n for n in cls.__graph.nodes if issubclass(n, end))
        return [[key for _, _, key in path] for path in nx.all_simple_edge_paths(cls.__graph, start, sinks)]

    @classmethod
    def dump_edges(cls, fp: TextIO = sys.stdout) -> None:
        fp.writelines(f'{u.__name__}-{data["translator"]}->{v.__name__}: {data["instance_count"]}\n'
                      for u, v, data in cls.__graph.edges(data=True))
