# T14: subscript uses a scalar that is not a loop variable. A[i+k, 1].
array A[1:10, 1:20] at 1000;
k = 2;
s = 0;
for i = 1 to 5 do
  s = s + A[i + k, 1];
end
