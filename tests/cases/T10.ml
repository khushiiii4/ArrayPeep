# T10: several arrays. C[i,j] = A[i,j] + B[i,j].
array A[1:4, 1:5] at 0;
array B[1:4, 1:5] at 100;
array C[1:4, 1:5] at 200;
for i = 1 to 4 do
  for j = 1 to 5 do
    C[i, j] = A[i, j] + B[i, j];
  end
end
