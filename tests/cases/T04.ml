# T04: linear subscripts with offsets. A[2*i+1, j-1], i = 1..4, j = 2..20.
array A[1:10, 1:20] at 1000;
s = 0;
for i = 1 to 4 do
  for j = 2 to 20 do
    s = s + A[2*i + 1, j - 1];
  end
end
