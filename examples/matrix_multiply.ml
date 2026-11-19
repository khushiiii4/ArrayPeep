# matrix_multiply.ml - Z = X * Y for 4 x 4 integer matrices (roadmap test T11).
# Memory layout: X at 0..15, Y at 16..31, Z at 32..47.
# Plans expected in a later phase: X: K = -5, D = (4, 0, 1)
#                                  Y: K = 11, D = (0, 1, 4)
#                                  Z: K = 27, D = (4, 1, 0)  in the k loop
# The roadmap writes Z[i,j] += X[i,k]*Y[k,j]; MiniLoop has no "+=", so the
# update is written out in full. Z[i,j] is cleared first so the result is
# exactly the matrix product.

array X[1:4, 1:4] at 0;
array Y[1:4, 1:4] at 16;
array Z[1:4, 1:4] at 32;

for i = 1 to 4 do
  for j = 1 to 4 do
    Z[i, j] = 0;
    for k = 1 to 4 do
      Z[i, j] = Z[i, j] + X[i, k] * Y[k, j];
    end
  end
end
