# T08: A[i,5] inside i and j loops; the address does not depend on j (Dj = 0).
array A[1:10, 1:20] at 1000;
s = 0;
for i = 1 to 10 do
  for j = 1 to 3 do
    s = s + A[i, 5];
  end
end
