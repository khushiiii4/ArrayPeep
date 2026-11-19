# A declaration produces no code; sequential loops reuse i; negative bounds; an empty body.
array A[1:3] at 0;
s = 0;
for i = -2 to 2 do
end
for i = 1 to 1 do
  s = s - -i;
end
