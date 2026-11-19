# T18: negative coefficient. A[11-i, j], i = 1..10, j = 1..20.
array A[1:10, 1:20] at 1000;
s = 0;
for i = 1 to 10 do
  for j = 1 to 20 do
    s = s + A[11 - i, j];
  end
end
