# T15: zero-trip loop (5 to 1).
array A[1:10, 1:20] at 1000;
s = 0;
for i = 5 to 1 do
  s = s + A[i, 1];
end
