# The inner loop never runs (lo > hi); the statement after it still runs.
for i = 1 to 2 do
  for j = 5 to 1 do
    t = t + i - j;
  end
  u = i;
end
