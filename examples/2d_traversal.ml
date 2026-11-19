# 2d_traversal.ml - row-major traversal of a 2-D array.
# A[1:10, 1:20] at 1000 has 200 elements stored at addresses 1000..1199.
# Expected address trace (see docs/hand_examples.md, Section 6):
#   200 reads of 1000, 1001, 1002, ..., 1199 in this order
#   (consecutive, because j - the last subscript - varies fastest).
# Expected final value of s depends on the initial memory contents
# (quad_spec.md, Section 4.4); with the default pattern s = 101387.

array A[1:10, 1:20] at 1000;

s = 0;
for i = 1 to 10 do
  for j = 1 to 20 do
    s = s + A[i, j];
  end
end
