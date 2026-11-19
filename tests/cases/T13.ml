# T13: indirect subscript (array element in subscript). W[i] = 9-i, then s += V[W[i]].
array W[0:9] at 0;
array V[0:9] at 10;
for i = 0 to 9 do
  W[i] = 9 - i;
end
s = 0;
for i = 0 to 9 do
  s = s + V[W[i]];
end
