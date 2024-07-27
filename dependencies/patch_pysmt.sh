#!/usr/bin/sed -f
/def Xor(self, left, right):/ c\
\    def Xor(self, *args):
/return self.Not(self.Iff(left, right))/ c\
\        exprs = self._polymorph_args_to_tuple(args)\
\        assert len(exprs) > 0\
\        if len(exprs) == 1:\
\            return exprs[0]\
\        elif len(exprs) == 2:\
\            a, b = exprs\
\            return self.Not(self.Iff(a, b))\
\        else:\
\            h = len(exprs) // 2\
\            return self.Xor(self.Xor(exprs[0:h]), self.Xor(exprs[h:]))

/return self.BVOr(self.BVAnd(left, self.BVNot(right)),/ c\
\        return self.BVNot(self.BVXor(left, right))
/self.BVAnd(self.BVNot(left), right))/ c\
