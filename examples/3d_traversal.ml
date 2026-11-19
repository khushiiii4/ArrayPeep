# 3d_traversal.ml - row-major traversal of a 3-D array (roadmap test T05).
# M[0:3, 0:4, 0:5] at 0 has d = (4, 5, 6), strides (30, 6, 1), 120 elements.
# Expected address trace: reads of 0, 1, 2, ..., 119 in this order.

array M[0:3, 0:4, 0:5] at 0;

s = 0;
for i = 0 to 3 do
  for j = 0 to 4 do
    for k = 0 to 5 do
      s = s + M[i, j, k];
    end
  end
end
