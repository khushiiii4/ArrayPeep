# T16: size-1 dimension and a single-iteration outer loop. U[i,j].
array U[1:1, 1:5] at 50;
s = 0;
for i = 1 to 1 do
  for j = 1 to 5 do
    s = s + U[i, j];
  end
end
