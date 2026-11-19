# T11: matrix multiplication Z = X * Y (MiniLoop has no +=, so Z is cleared first).
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
