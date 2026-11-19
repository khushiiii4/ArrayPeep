# T09: transposed access. S[j,i] with i outer, j inner.
array S[1:8, 1:8] at 300;
s = 0;
for i = 1 to 8 do
  for j = 1 to 8 do
    s = s + S[j, i];
  end
end
