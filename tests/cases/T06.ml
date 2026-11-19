# T06: negative bounds. N[i,j], i = -5..5, j = -3..3.
array N[-5:5, -3:3] at 200;
s = 0;
for i = -5 to 5 do
  for j = -3 to 3 do
    s = s + N[i, j];
  end
end
