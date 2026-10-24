import sys
rows = {}
for line in open(sys.argv[1]):
    v, label, out = line.rstrip('\n').split('\t')
    rows.setdefault(v, {})[label] = out
L, ST = 'org.example.launcher', 'com.webos.app.home'
def swap_label(lb):
    return lb.replace('front=stock', 'front=@').replace('front=L', 'front=stock').replace('front=@', 'front=L').replace('choice=L', 'choice=-')
def swap_out(o):
    return o.replace(ST, '@').replace(L, ST).replace('@', L)
res = []
d = [lb for lb in rows['min'] if lb.startswith('choice=-') and rows['min'][lb] != rows['stock'][lb]]
res.append('no-choice==stock: %s' % ('yes' if not d else 'NO %d e.g. %s' % (len(d), d[0])))
d = [lb for lb in rows['min'] if lb.startswith('choice=L') and swap_out(rows['min'][lb]) != rows['stock'][swap_label(lb)]]
res.append('choice==stock-with-roles-swapped: %s' % ('yes' if not d else 'NO %d e.g. %s | min=%s | stock(swapped)=%s' % (len(d), d[0], rows['min'][d[0]], rows['stock'][swap_label(d[0])])))
d = [lb for lb in rows['min'] if 'THROW' in rows['min'][lb]]
if d: res.append('THROWS %d' % len(d))
print(sys.argv[2] + ': ' + '; '.join(res))

assert set(rows) >= {'min', 'stock'}, 'missing variants'
assert all(len(r) == 144 for r in rows.values()), 'incomplete scenario matrix'
assert not any('THROW' in v for r in rows.values() for v in r.values()), 'harness threw'
assert not any('NO ' in r for r in res), 'behaviour differs'
