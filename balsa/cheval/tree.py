from __future__ import division, absolute_import, print_function, unicode_literals

from six import iterkeys, itervalues

import numpy as np
import pandas as pd


class _ChoiceNode(object):

    def __init__(self, *args):
        self._name, self._root, self._parent, self.logsum_scale, self._level = args
        self._children = set()

    def __str__(self):
        return self._name

    def __repr__(self):
        return "ChoiceNode(%s)" % self._name

    @property
    def name(self):
        return self._name

    @property
    def root(self):
        return self._root

    @property
    def parent(self):
        return self._parent

    @property
    def level(self):
        return self._level

    @property
    def is_parent(self):
        return len(self._children) > 0

    def max_level(self):
        max_level = self._level

        for c in self.children():
            max_level = max(max_level, c.max_level())

        return max_level

    def children(self):
        """
        Iterates through child nodes

        Yields:
            _ChoiceNode: This node's children, if they exist

        """
        for c in self._children:
            yield c

    def add_node(self, name, logsum_scale=1.0):
        """
        Adds a nested alternative to the logit model. The name must be unique.

        Args:
            name (str): The name of the alternative in the choice set.
            logsum_scale (int): The logsum scale parameter. Not used in the probability computation if this node has no
                children. A value of 1.0 can be used if the estimated coefficients are already scaled.

        Returns:
            The added node, which also has an 'add_node' method.

        """
        return self._root._root_add(self, name, logsum_scale, self._level + 1)


class ChoiceTree(object):

    def __init__(self, root):
        self._root = root

        self._all_nodes = {}
        self._children = set()
        self._cached_node_index = None

    def __getitem__(self, item):
        return self._all_nodes[item]

    def max_level(self):
        """
        Gets the maximum depth of the tree, with 1 being the lowest valid level.

        Returns:
            int

        """

        max_level = 1

        for c in self.children():
            max_level = max(max_level, c.max_level())

        return max_level

    def children(self):
        """
        Iterates through child nodes

        Yields:
            _ChoiceNode: Top-level child nodes

        """
        for c in self._children:
            yield c

    @property
    def node_index(self):
        if self._cached_node_index is None:
            idx = pd.Index(sorted(iterkeys(self._all_nodes)))
            self._cached_node_index = idx
            return idx
        return self._cached_node_index

    def _root_add(self, parent, new_name, logsum_scale, level):
        assert 0 < logsum_scale <= 1, "Logsum scale must be on the interval (0, 1]; got %s instead" % logsum_scale
        if new_name in self._all_nodes:
            old_node = self._all_nodes[new_name]
            old_node.parent._children.remove(old_node)
        new_node = _ChoiceNode(new_name, self, parent, logsum_scale, level)
        parent._children.add(new_node)
        self._all_nodes[new_name] = new_node

        # Clear the cached node index because the set of alternatives is being changed
        self._cached_node_index = None
        return new_node

    def add_node(self, name, logsum_scale=1.0):
        """
        Adds a top-level alternative to the logit model. The name must be unique.

        Args:
            name (str): The name of the alternative in the choice set.
            logsum_scale (int): The logsum scale parameter. Not used in the probability computation if this node has no
                children. A value of 1.0 can be used if the estimated coefficients are already scaled.

        Returns:
            _ChoiceNode: The added node, which also has an 'add_node' method.

        """
        return self._root_add(self, name, logsum_scale, 0)

    def add_nodes(self, names, logsum_scales=None):
        """
        Convenience function to "batch" add several alternatives at once (useful for Multinomial models with large
        choice sets).

        Args:
            names (Iterable[str]): Iterable of names to add at once.
            logsum_scales (Iterable[float] or None): Iterable of logsum scales to use for the new nodes. `names` and
                `logsum_scales` are `zip()`ed together, so use an ordered collection (like a List) for both. For most
                use cases, the default value of None is sufficient.

        Returns:
            dict[str, _ChoiceNode]: Dictionary whose keys are the names used, and whose values are the nodes that were
                created.

        """
        if isinstance(names, str):
            raise TypeError("To add a single node, use the singular `add_node` method")
        if logsum_scales is None: logsum_scales = [1.0] * len(names)

        nodes = {}
        for name, scale in zip(names, logsum_scales):
            node = self.add_node(name, logsum_scale=scale)
            nodes[name] = node
        return nodes

    def remove_node(self, name):
        """
        Removes a node (at any level) from the logit model.

        Args:
            name (str): The name of the node to remove

        """

        old_node = self._all_nodes[name]
        old_node.parent._children.remove(old_node)
        del self._all_nodes[name]

        # Clear the cached node index because the set of alternatives is being changed
        self._cached_node_index = None

    def flatten(self):
        node_index = self.node_index
        node_positions = {name: i for i, name in enumerate(node_index)}

        hierarchy = np.full(len(node_index), -1, dtype='i8')
        levels = np.zeros(len(node_index), dtype='i8')
        logsum_scales = np.ones(len(node_index), dtype='f8')

        for node in itervalues(self._all_nodes):
            position = node_positions[node.name]
            levels[position] = node.level

            if node.parent is not self:
                parent_position = node_positions[node.parent.name]
                hierarchy[position] = parent_position

            if node.is_parent:
                logsum_scales[position] = node.logsum_scale

        return hierarchy, levels, logsum_scales
