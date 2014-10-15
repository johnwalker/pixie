py_object = object
import pixie.vm.object as object
from pixie.vm.object import affirm
from pixie.vm.primitives import nil, true, false
import pixie.vm.code as code
from pixie.vm.numbers import Integer
import pixie.vm.protocols as proto
from  pixie.vm.code import extend, as_var
from rpython.rlib.rarithmetic import r_uint, intmask
import rpython.rlib.jit as jit
import pixie.vm.rt as rt

MASK_32 = 0xFFFFFFFF

class Box(py_object):
    def __init__(self):
        self._val = None

class PersistentHashMap(object.Object):
    _type = object.Type(u"pixie.stdlib.PersistentHashMap")

    def type(self):
        return PersistentHashMap._type

    def __init__(self, cnt, root, meta=nil):
        self._cnt = cnt
        self._root = root
        self._meta = meta

    def assoc(self, key, val):
        added_leaf = Box()

        new_root = (BitmapIndexedNode_EMPTY if self._root is None else self._root) \
                   .assoc_inode(0, rt.hash(key), key, val, added_leaf)

        if new_root is self._root:
            return self

        return PersistentHashMap(self._cnt if added_leaf._val is None else self._cnt + 1, new_root, self._meta)

    def val_at(self, key, not_found):
        return not_found if self._root is None else self._root.find(0, rt.hash(key), key, not_found)





class INode(object.Object):
    _type = object.Type(u"pixie.stdlib.INode")

    def type(self):
        return INode._type

    def assoc_inode(self, shift, hash_val, key, val, added_leaf):
        pass

    def find(self, shift, hash_val, key, not_found):
        pass

def mask(hash, shift):
    return (hash >> shift) & 0x01f

def bitpos(hash, shift):
    return (1 << mask(hash, shift)) & MASK_32



class BitmapIndexedNode(INode):

    def __init__(self, edit,  bitmap, array):
        self._edit = edit
        self._bitmap = bitmap
        self._array = array

    def index(self, bit):
        return bit_count(self._bitmap & (bit - 1))

    def assoc_inode(self, shift, hash_val, key, val, added_leaf):
        bit = bitpos(hash_val, shift)
        idx = self.index(bit)

        if (self._bitmap & bit) != 0:
            key_or_null = self._array[2 * idx]
            val_or_node = self._array[2 * idx + 1]

            if key_or_null is None:
                assert isinstance(val_or_node, INode)
                n = val_or_node.assoc_inode(shift + 5, hash_val, key, val, added_leaf)
                if n is val_or_node:
                    return self
                return BitmapIndexedNode(None, self._bitmap, clone_and_set(self._array, 2 * idx + 1, n))


            if key_or_null is None or rt.eq(key, key_or_null):
                if val is val_or_node:
                    return self
                return BitmapIndexedNode(None, self._bitmap, clone_and_set(self._array, 2 * idx + 1, val))

            added_leaf._val = added_leaf
            return BitmapIndexedNode(None, self._bitmap,
                clone_and_set2(self._array,
                               2 * idx, None,
                               2 * idx + 1, self.create_node(shift+ 5, key_or_null, val_or_node, hash_val, key, val)))
        else:
            n = bit_count(self._bitmap)
            if n >= 16:
                nodes = [None] * 32
                jdx = mask(hash_val, shift)
                nodes[jdx] = BitmapIndexedNode_EMPTY.assoc_inode(shift + 5, hash_val, key, val, added_leaf)
                j = 0

                for i in range(32):
                    if (self._bitmap >> i) & 1 != 0:
                        if self._array[j] is None:
                            nodes[i] = self._array[j + 1]
                        else:
                            nodes[i] = BitmapIndexedNode_EMPTY.assoc_inode(shift + 5, rt.hash(self._array[j]),
                                                               self._array[j], self._array[j + 1], added_leaf)
                        j += 1

                return ArrayNode(None, n + 1, nodes)
            else:
                new_array = [None] * (2 * (n + 1))
                code.list_copy(self._array, 0, new_array, 0, 2 * idx)
                new_array[2 * idx] = key
                added_leaf._val = added_leaf
                new_array[2 * idx + 1] = val
                code.list_copy(self._array, 2 * idx, new_array, 2 * (idx + 1), 2 * (n - idx))
                return BitmapIndexedNode(None, self._bitmap | bit, new_array)

    def find(self, shift, hash_val, key, not_found):
        bit = bitpos(hash_val, shift)
        if (self._bitmap & bit) == 0:
            return not_found
        idx = self.index(bit)
        key_or_null = self._array[2 * idx]
        val_or_node = self._array[2 * idx + 1]
        if key_or_null is None:
            return val_or_node.find(shift + 5, hash_val, key, not_found)
        if rt.eq(key, key_or_null):
            return val_or_node
        return not_found


BitmapIndexedNode_EMPTY = BitmapIndexedNode(None, r_uint(0), [])


class ArrayNode(INode):
    def __init__(self, edit, cnt, array):
        self._cnt = cnt
        self._edit = edit
        self._array = array

    def assoc_inode(self, shift, hash_val, key, val, added_leaf):
        idx = mask(hash_val, shift)
        node = self._array[idx]
        if node is None:
            return ArrayNode(None, self._cnt + 1, clone_and_set(self._array, idx,
                            BitmapIndexedNode_EMPTY.assoc_inode(shift + 5, hash_val, key, val, added_leaf)))

        n = node.assoc_inode(shift + 5, hash_val, key, val, added_leaf)
        if n is node:
            return self
        return ArrayNode(None, self._cnt, clone_and_set(self._array, idx, n))

    def find(self, shift, hash_val, key, not_found):
        idx = mask(hash_val, shift)
        node = self._array[idx]
        if node is None:
            return not_found
        return node.find(shift + 5, hash_val, key, not_found)



def bit_count(i):
    assert isinstance(i, r_uint)
    i = i - ((i >> 1) & 0x55555555)
    i = (i & 0x33333333) + ((i >> 2) & 0x33333333)
    return (((i + (i >> 4) & 0xF0F0F0F) * 0x1010101) & 0xffffffff) >> 24

@jit.unroll_safe
def clone_and_set(array, i, a):
    clone = [None] * len(array)

    idx = 0
    while idx < len(array):
        clone[idx] = array[idx]

    clone[i] = a
    return clone

@jit.unroll_safe
def clone_and_set2(array, i, a, j, b):
    clone = [None] * len(array)

    idx = 0
    while idx < len(array):
        clone[idx] = array[idx]

    clone[i] = a
    clone[j] = b
    return clone


### hook into RT

EMPTY = PersistentHashMap(r_uint(0), None)

@as_var("hashmap")
def hashmap__args(args):
    affirm(len(args) & 0x1 == 0, u"hashmap requires even number of args")

    idx = 0
    acc = EMPTY

    while idx < len(args):
        key = args[idx]
        val = args[idx + 1]

        acc = acc.assoc(key, val)

        idx += 2

    return acc

@extend(proto._val_at, PersistentHashMap)
def _val_at(self, key, not_found):
    return self.val_at(key, not_found)