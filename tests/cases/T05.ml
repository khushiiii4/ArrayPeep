# T05: 3-D traversal. M[i,j,k], i = 0..3, j = 0..4, k = 0..5.
array M[0:3, 0:4, 0:5] at 0;
s = 0;
for i = 0 to 3 do
  for j = 0 to 4 do
    for k = 0 to 5 do
      s = s + M[i, j, k];
    end
  end
end
