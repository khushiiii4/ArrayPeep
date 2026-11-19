# T12: non-linear subscript (product of loop variables). V[i*j].
array V[0:99] at 0;
s = 0;
for i = 0 to 9 do
  for j = 0 to 9 do
    s = s + V[i * j];
  end
end
