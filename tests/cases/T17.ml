# T17: 4-D array, four nested loops.
array H[0:1, 0:1, 0:1, 0:2] at 0;
s = 0;
for i = 0 to 1 do
  for j = 0 to 1 do
    for k = 0 to 1 do
      for m = 0 to 2 do
        s = s + H[i, j, k, m];
      end
    end
  end
end
