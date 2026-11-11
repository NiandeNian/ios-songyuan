#!/usr/bin/env python3
"""Extract hardware-only evidence from a live FDT; omit chosen/identity data."""
import argparse
import json
from pathlib import Path
from fdt import nodes, cells, readable, text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dtb', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    tree = nodes(args.dtb.read_bytes())
    selected = {}
    for path, props in tree.items():
        compatible = text(props.get('compatible', b'')).lower()
        direct = (path in ('/', '/memory', '/timer', '/psci') or
                  path.startswith('/reserved-memory/') or
                  path.startswith('/cpus/cpu@') and path.count('/') == 2)
        match = any(k in (path + ' ' + compatible).lower() for k in
                    ('syna', 'focal', 'fts', 'touch', 'goodix', 'novatek',
                     'interrupt-controller@17000000', 'ufshc@1d84000'))
        panel = 'mdss_dsi_songyuan_42_0d_0a_p2_dsc_cmd' in path
        if not (direct or match or panel):
            continue
        selected[path] = {name: readable(value, name) for name, value in props.items()
                          if name not in ('phandle', 'linux,phandle', 'name') and
                          (not panel or name in ('qcom,mdss-dsi-panel-name',
                           'qcom,mdss-dsi-panel-width', 'qcom,mdss-dsi-panel-height',
                           'mi,max-brightness-normal', 'qcom,platform-reset-gpio',
                           'qcom,mdss-dsi-reset-sequence', 'qcom,mdss-dsi-panel-framerate',
                           'qcom,mdss-dsi-bl-max-level', 'qcom,mdss-dsi-bl-min-level'))}
    # GPIO/supply/pinctrl references are meaningful only with their target nodes.
    handles = {cells(p['phandle'])[0]: path for path, p in tree.items() if 'phandle' in p}
    references = {}
    for path in list(selected):
        for name, value in tree[path].items():
            if ('gpio' in name or name.endswith('-supply') or name.startswith('pinctrl-')) and len(value) >= 4 and len(value) % 4 == 0 and name != 'pinctrl-names':
                handle = cells(value)[0]
                if handle in handles:
                    target = handles[handle]
                    references[f'{path}:{name}'] = target
                    selected[target] = {k: readable(v, k) for k, v in tree[target].items()
                                        if k not in ('phandle', 'linux,phandle', 'name')}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({'source': 'live FDT captured 2026-11-11',
                                  'nodes': selected, 'references': references}, indent=2) + '\n')
    for path, props in selected.items():
        if 'compatible' in props or path == '/memory' or path.startswith('/cpus/cpu@'):
            print(path, json.dumps({k: v for k, v in props.items() if k in
                  ('compatible', 'status', 'reg', 'interrupts', 'reset-gpio', 'irq-gpio',
                   'avdd-gpio', 'clock-frequency', 'enable-method', 'focaltech,reset-gpio',
                   'focaltech,irq-gpio', 'focaltech,display-coords')}))


if __name__ == '__main__':
    main()
