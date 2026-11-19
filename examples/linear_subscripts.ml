# linear_subscripts.ml - 2-D access with linear subscripts (roadmap test T04).
# The first subscript is 2*i + 1 and the second is j - 1, so the address is
#   998 + 40*i + j      (K = 998, D_i = 40, D_j = 1; docs/hand_examples.md, Section 4).
# The access executes 4 * 19 = 76 times; first address 1040 (A[3,1]),
# last address 1178 (A[9,19]).

array A[1:10, 1:20] at 1000;

s = 0;
for i = 1 to 4 do
  for j = 2 to 20 do
    s = s + A[2*i + 1, j - 1];
  end
end
