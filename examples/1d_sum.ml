# 1d_sum.ml - fill a 1-D array, then sum it.
# Array V has 10 elements, indices 5..14, stored at addresses 500..509.
# Expected address trace (see docs/hand_examples.md, Section 5):
#   10 writes to 500, 501, ..., 509   (fill loop)
#   10 reads  from 500, 501, ..., 509 (sum loop)
# Expected final value: s = 2*(5+6+...+14) = 190

array V[5:14] at 500;

for i = 5 to 14 do
  V[i] = 2 * i;
end

s = 0;
for i = 5 to 14 do
  s = s + V[i];
end
