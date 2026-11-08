"""
Origin Bridge Template Catalog.

A curated library of 30+ scientific chart templates inspired by the
editaplot project. Each template declares:
- name: identifier
- category: grouping (e.g. 'spectroscopy', 'distribution')
- description: human-readable
- required_columns: list of required input columns
- optional_columns: list of optional columns
- suggested_fit: 'linear' | 'gauss' | 'lorentz' | etc.
- default_axis_titles: dict
- default_plot_kind: 'scatter' | 'line' | 'bar' | 'bar+fit' | etc.
- verified_on: origin version last verified

This module exposes:
- TEMPLATES: list of template dicts
- list_templates(category): names grouped by category
- describe_template(name): return contract for one
- validate_data_shape(name, data, columns): dry-run check

render_template used to live here too, but it called op.new() (throwing away
whatever the user had open) and imported a fit module this package never had;
the bridge renders presets through originlab.chart_render instead, so the dead
code was removed (2026-11-08).
"""
from __future__ import annotations
import os
from typing import Dict, List, Optional, Any


# Each template is a dict
TEMPLATES: List[Dict[str, Any]] = [
    # ===== Spectroscopy =====
    {
        'name': 'xps',
        'category': 'spectroscopy',
        'description': 'X-ray Photoelectron Spectroscopy (binding energy vs intensity)',
        'required_columns': ['binding_energy', 'intensity'],
        'optional_columns': ['fit'],
        'suggested_fit': None,
        'default_axis_titles': {'x': '结合能 / eV', 'y': '强度 / cps'},
        'default_plot_kind': 'line',
        'verified_on': '2024b',
        'extra': 'binding_energy is typically inverted (high → low)'
    },
    {
        'name': 'xrd',
        'category': 'spectroscopy',
        'description': 'X-ray Diffraction (2θ vs intensity)',
        'required_columns': ['two_theta', 'intensity'],
        'optional_columns': ['fit'],
        'suggested_fit': 'gauss',   # peak fitting
        'default_axis_titles': {'x': '2θ / °', 'y': '强度 / cps'},
        'default_plot_kind': 'line',
        'verified_on': '2024b',
    },
    {
        'name': 'xas',
        'category': 'spectroscopy',
        'description': 'X-ray Absorption Spectroscopy (E vs μ)',
        'required_columns': ['energy', 'mu'],
        'optional_columns': ['fit'],
        'suggested_fit': None,
        'default_axis_titles': {'x': '能量 / eV', 'y': 'μ(E)'},
        'default_plot_kind': 'line',
        'verified_on': '2024b',
    },
    {
        'name': 'ftir',
        'category': 'spectroscopy',
        'description': 'FTIR / IR spectroscopy (wavenumber vs absorbance/transmittance)',
        'required_columns': ['wavenumber', 'intensity'],
        'optional_columns': ['fit'],
        'suggested_fit': None,
        'default_axis_titles': {'x': '波数 / cm⁻¹', 'y': '吸光度'},
        'default_plot_kind': 'line',
        'verified_on': '2024b',
    },
    {
        'name': 'uv_vis',
        'category': 'spectroscopy',
        'description': 'UV-Vis absorbance (wavelength vs absorbance)',
        'required_columns': ['wavelength', 'absorbance'],
        'optional_columns': ['fit'],
        'suggested_fit': 'gauss',
        'default_axis_titles': {'x': '波长 / nm', 'y': '吸光度'},
        'default_plot_kind': 'line',
        'verified_on': '2024b',
    },
    {
        'name': 'nmr',
        'category': 'spectroscopy',
        'description': 'NMR spectrum (chemical shift vs intensity)',
        'required_columns': ['ppm', 'intensity'],
        'optional_columns': ['fit'],
        'suggested_fit': 'lorentz',
        'default_axis_titles': {'x': '化学位移 / ppm', 'y': '强度'},
        'default_plot_kind': 'line',
        'verified_on': '2024b',
    },
    {
        'name': 'pl',
        'category': 'spectroscopy',
        'description': 'Photoluminescence spectrum (wavelength vs intensity)',
        'required_columns': ['wavelength', 'intensity'],
        'optional_columns': ['fit'],
        'suggested_fit': 'gauss',
        'default_axis_titles': {'x': '波长 / nm', 'y': '强度 / cps'},
        'default_plot_kind': 'line',
        'verified_on': '2024b',
    },
    {
        'name': 'trpl',
        'category': 'spectroscopy',
        'description': 'Time-Resolved PL (time vs intensity, often log Y)',
        'required_columns': ['time', 'intensity'],
        'optional_columns': ['fit'],
        'suggested_fit': 'expdec1',
        'default_axis_titles': {'x': '时间 / ns', 'y': '强度 / cps'},
        'default_plot_kind': 'log',
        'verified_on': '2024b',
    },

    # ===== Thermal =====
    {
        'name': 'dsc',
        'category': 'thermal',
        'description': 'Differential Scanning Calorimetry (T vs heat flow)',
        'required_columns': ['temperature', 'heat_flow'],
        'optional_columns': ['fit'],
        'suggested_fit': None,
        'default_axis_titles': {'x': '温度 / °C', 'y': '热流 / mW'},
        'default_plot_kind': 'line',
        'verified_on': '2024b',
    },
    {
        'name': 'tga',
        'category': 'thermal',
        'description': 'Thermogravimetric Analysis (T vs mass %)',
        'required_columns': ['temperature', 'mass_percent'],
        'optional_columns': ['dtg'],
        'suggested_fit': None,
        'default_axis_titles': {'x': '温度 / °C', 'y': '质量 / %'},
        'default_plot_kind': 'line',
        'verified_on': '2024b',
    },
    {
        'name': 'tga_dtg',
        'category': 'thermal',
        'description': 'TGA + DTG dual-Y (T vs mass% with derivative on right)',
        'required_columns': ['temperature', 'mass_percent', 'dtg'],
        'optional_columns': [],
        'suggested_fit': None,
        'default_axis_titles': {'x': '温度 / °C', 'y': '质量 / %', 'y2': 'DTG / %/°C'},
        'default_plot_kind': 'dual_y',
        'verified_on': '2024b',
    },

    # ===== Electrochemistry =====
    {
        'name': 'cv',
        'category': 'electrochemistry',
        'description': 'Cyclic Voltammetry (E vs i)',
        'required_columns': ['potential', 'current'],
        'optional_columns': ['fit'],
        'suggested_fit': None,
        'default_axis_titles': {'x': '电位 / V', 'y': '电流 / A'},
        'default_plot_kind': 'line',
        'verified_on': '2024b',
    },
    {
        'name': 'lsv',
        'category': 'electrochemistry',
        'description': 'Linear Sweep Voltammetry',
        'required_columns': ['potential', 'current'],
        'optional_columns': [],
        'suggested_fit': 'linear',
        'default_axis_titles': {'x': '过电位 / V', 'y': '电流密度 / mA/cm²'},
        'default_plot_kind': 'line',
        'verified_on': '2024b',
    },
    {
        'name': 'eis',
        'category': 'electrochemistry',
        'description': 'Electrochemical Impedance Spectroscopy (Nyquist plot)',
        'required_columns': ['z_real', 'z_imag'],
        'optional_columns': [],
        'suggested_fit': None,
        'default_axis_titles': {'x': "Z' / Ω", 'y': "-Z'' / Ω"},
        'default_plot_kind': 'scatter',
        'verified_on': '2024b',
    },

    # ===== Distribution / Relationship =====
    {
        'name': 'histogram',
        'category': 'distribution',
        'description': 'Single-column histogram',
        'required_columns': ['value'],
        'optional_columns': ['bin_edges'],
        'suggested_fit': 'gauss',
        'default_axis_titles': {'x': '值', 'y': '频次'},
        'default_plot_kind': 'bar',
        'verified_on': '2024b',
    },
    {
        'name': 'particle_size',
        'category': 'distribution',
        'description': 'Particle size distribution (log-normal fit)',
        'required_columns': ['diameter'],
        'optional_columns': ['count'],
        'suggested_fit': None,
        'default_axis_titles': {'x': '粒径 / nm', 'y': '频次 / %'},
        'default_plot_kind': 'hist',
        'verified_on': '2024b',
    },
    {
        'name': 'violin',
        'category': 'distribution',
        'description': 'Violin plot (multiple groups)',
        'required_columns': ['group', 'value'],
        'optional_columns': [],
        'suggested_fit': None,
        'default_axis_titles': {'x': '组', 'y': '值'},
        'default_plot_kind': 'box',
        'verified_on': '2024b',
    },
    {
        'name': 'grouped_box',
        'category': 'distribution',
        'description': 'Grouped box plot',
        'required_columns': ['group', 'value'],
        'optional_columns': [],
        'suggested_fit': None,
        'default_axis_titles': {'x': '组', 'y': '值'},
        'default_plot_kind': 'box',
        'verified_on': '2024b',
    },
    {
        'name': 'density_ridgeline',
        'category': 'distribution',
        'description': 'Density ridgeline plot (stacked KDEs)',
        'required_columns': ['group', 'value'],
        'optional_columns': [],
        'suggested_fit': None,
        'default_axis_titles': {'x': '值', 'y': '组'},
        'default_plot_kind': 'ridgeline',
        'verified_on': '2024b',
    },

    # ===== Relationship =====
    {
        'name': 'scatter',
        'category': 'relationship',
        'description': 'Generic X/Y scatter',
        'required_columns': ['x', 'y'],
        'optional_columns': ['fit'],
        'suggested_fit': None,
        'default_axis_titles': {'x': 'X', 'y': 'Y'},
        'default_plot_kind': 'scatter',
        'verified_on': '2024b',
    },
    {
        'name': 'scatter_matrix',
        'category': 'relationship',
        'description': 'Scatter matrix / pairs plot',
        'required_columns': ['x1', 'x2', 'x3'],
        'optional_columns': ['x4', 'x5'],
        'suggested_fit': None,
        'default_axis_titles': {'x': '变量', 'y': '变量'},
        'default_plot_kind': 'matrix',
        'verified_on': '2024b',
    },
    {
        'name': 'bubble',
        'category': 'relationship',
        'description': 'Bubble chart (X/Y with size dimension)',
        'required_columns': ['x', 'y', 'size'],
        'optional_columns': ['category'],
        'suggested_fit': None,
        'default_axis_titles': {'x': 'X', 'y': 'Y'},
        'default_plot_kind': 'bubble',
        'verified_on': '2024b',
    },
    {
        'name': 'trajectory',
        'category': 'relationship',
        'description': 'Trajectory (parameter evolving over time)',
        'required_columns': ['time', 'value'],
        'optional_columns': ['group'],
        'suggested_fit': None,
        'default_axis_titles': {'x': '时间', 'y': '值'},
        'default_plot_kind': 'line',
        'verified_on': '2024b',
    },
    {
        'name': 'paired_trajectory',
        'category': 'relationship',
        'description': 'Paired before/after trajectory',
        'required_columns': ['time', 'group', 'value'],
        'optional_columns': [],
        'suggested_fit': None,
        'default_axis_titles': {'x': '时间', 'y': '值'},
        'default_plot_kind': 'paired_line',
        'verified_on': '2024b',
    },
    {
        'name': 'calibration_curve',
        'category': 'relationship',
        'description': 'Calibration curve with confidence band',
        'required_columns': ['concentration', 'response'],
        'optional_columns': ['fit'],
        'suggested_fit': 'linear',
        'default_axis_titles': {'x': '浓度', 'y': '响应'},
        'default_plot_kind': 'line+fit',
        'verified_on': '2024b',
    },
    {
        'name': 'bland_altman',
        'category': 'relationship',
        'description': 'Bland-Altman plot (method comparison)',
        'required_columns': ['method1', 'method2'],
        'optional_columns': [],
        'suggested_fit': 'linear',
        'default_axis_titles': {'x': '平均值', 'y': '差值'},
        'default_plot_kind': 'scatter',
        'verified_on': '2024b',
    },

    # ===== Categorical =====
    {
        'name': 'bar',
        'category': 'categorical',
        'description': 'Single bar chart',
        'required_columns': ['category', 'value'],
        'optional_columns': ['error'],
        'suggested_fit': None,
        'default_axis_titles': {'x': '类别', 'y': '值'},
        'default_plot_kind': 'bar',
        'verified_on': '2024b',
    },
    {
        'name': 'grouped_bar',
        'category': 'categorical',
        'description': 'Grouped bar chart',
        'required_columns': ['category', 'group', 'value'],
        'optional_columns': ['error'],
        'suggested_fit': None,
        'default_axis_titles': {'x': '类别', 'y': '值'},
        'default_plot_kind': 'bar',
        'verified_on': '2024b',
    },
    {
        'name': 'stacked_bar',
        'category': 'categorical',
        'description': 'Stacked bar chart',
        'required_columns': ['category', 'group', 'value'],
        'optional_columns': [],
        'suggested_fit': None,
        'default_axis_titles': {'x': '类别', 'y': '值'},
        'default_plot_kind': 'stacked_bar',
        'verified_on': '2024b',
    },
    {
        'name': 'percent_stacked_bar',
        'category': 'categorical',
        'description': '100% stacked bar',
        'required_columns': ['category', 'group', 'value'],
        'optional_columns': [],
        'suggested_fit': None,
        'default_axis_titles': {'x': '类别', 'y': '比例 / %'},
        'default_plot_kind': 'percent_stacked_bar',
        'verified_on': '2024b',
    },
    {
        'name': 'horizontal_bar',
        'category': 'categorical',
        'description': 'Horizontal bar',
        'required_columns': ['category', 'value'],
        'optional_columns': ['error'],
        'suggested_fit': None,
        'default_axis_titles': {'x': '值', 'y': '类别'},
        'default_plot_kind': 'horizontal_bar',
        'verified_on': '2024b',
    },
    {
        'name': 'line_error',
        'category': 'categorical',
        'description': 'Line with error bars',
        'required_columns': ['x', 'y', 'error'],
        'optional_columns': [],
        'suggested_fit': None,
        'default_axis_titles': {'x': 'X', 'y': 'Y'},
        'default_plot_kind': 'line_error',
        'verified_on': '2024b',
    },
    {
        'name': 'radar',
        'category': 'categorical',
        'description': 'Radar / spider chart',
        'required_columns': ['axis', 'value'],
        'optional_columns': ['group'],
        'suggested_fit': None,
        'default_axis_titles': {'x': '', 'y': '值'},
        'default_plot_kind': 'radar',
        'verified_on': '2024b',
    },
    {
        'name': 'sankey',
        'category': 'categorical',
        'description': 'Sankey flow diagram (source/target/value)',
        'required_columns': ['source', 'target', 'value'],
        'optional_columns': [],
        'suggested_fit': None,
        'default_axis_titles': {'x': '', 'y': ''},
        'default_plot_kind': 'sankey',
        'verified_on': '2024b',
    },

    # ===== Medical / Statistics =====
    {
        'name': 'forest',
        'category': 'medical',
        'description': 'Forest plot (meta-analysis)',
        'required_columns': ['study', 'effect', 'lower', 'upper'],
        'optional_columns': [],
        'suggested_fit': None,
        'default_axis_titles': {'x': '效应量', 'y': '研究'},
        'default_plot_kind': 'forest',
        'verified_on': '2024b',
    },
    {
        'name': 'roc_curve',
        'category': 'medical',
        'description': 'ROC / diagnostic curve',
        'required_columns': ['fpr', 'tpr'],
        'optional_columns': [],
        'suggested_fit': None,
        'default_axis_titles': {'x': '1 - 特异度', 'y': '灵敏度'},
        'default_plot_kind': 'line',
        'verified_on': '2024b',
    },
    {
        'name': 'kaplan_meier',
        'category': 'medical',
        'description': 'Kaplan-Meier survival curve',
        'required_columns': ['time', 'survival'],
        'optional_columns': ['group'],
        'suggested_fit': 'expdec1',
        'default_axis_titles': {'x': '时间', 'y': '生存率'},
        'default_plot_kind': 'step',
        'verified_on': '2024b',
    },
    {
        'name': 'confusion_matrix',
        'category': 'medical',
        'description': 'Confusion matrix heatmap',
        'required_columns': ['matrix'],
        'optional_columns': [],
        'suggested_fit': None,
        'default_axis_titles': {'x': '预测', 'y': '真实'},
        'default_plot_kind': 'heatmap',
        'verified_on': '2024b',
    },
    {
        'name': 'decision_curve',
        'category': 'medical',
        'description': 'Decision curve analysis',
        'required_columns': ['threshold', 'net_benefit'],
        'optional_columns': ['group'],
        'suggested_fit': None,
        'default_axis_titles': {'x': '阈值', 'y': '净收益'},
        'default_plot_kind': 'line',
        'verified_on': '2024b',
    },

    # ===== 3D =====
    {
        'name': 'trajectory3d',
        'category': '3d',
        'description': '3D trajectory',
        'required_columns': ['x', 'y', 'z'],
        'optional_columns': ['time'],
        'suggested_fit': None,
        'default_axis_titles': {'x': 'X', 'y': 'Y', 'z': 'Z'},
        'default_plot_kind': '3d_line',
        'verified_on': '2024b',
    },
]


def list_templates(category: Optional[str] = None) -> List[Dict[str, str]]:
    """Return summary list for MCP tool."""
    items = []
    for t in TEMPLATES:
        if category and t['category'] != category:
            continue
        items.append({
            'name': t['name'],
            'category': t['category'],
            'description': t['description'],
            'verified_on': t['verified_on'],
            'required_columns': t['required_columns'],
        })
    return items


def describe_template(name: str) -> Optional[Dict]:
    """Return full contract for one template."""
    for t in TEMPLATES:
        if t['name'] == name:
            return t
    return None


def validate_data_shape(name: str, data: Any) -> Dict[str, Any]:
    """Dry-run check: does the data have the required shape?

    Args:
        name: template name
        data: either a file path (.dat/.csv/.xlsx) or a 2D nested list
    Returns:
        dict with status, issues, columns_detected, fit
    """
    tpl = describe_template(name)
    if tpl is None:
        return {'status': 'error', 'message': f'unknown template: {name}'}

    result = {
        'status': 'ok',
        'template': name,
        'required_columns': tpl['required_columns'],
        'suggested_fit': tpl['suggested_fit'],
        'issues': [],
    }

    # Detect data shape
    columns_detected = []
    n_rows = 0

    if isinstance(data, str):
        # File path
        from .io import read_dat, read_csv, read_excel
        ext = os.path.splitext(data)[1].lower()
        try:
            if ext == '.dat':
                longs, _, d = read_dat(data)
                columns_detected = longs
                n_rows = d.shape[0]
            elif ext in ('.csv', '.tsv'):
                import pandas as pd
                sep = '\t' if ext == '.tsv' else ','
                df = pd.read_csv(data, sep=sep)
                columns_detected = list(df.columns)
                n_rows = len(df)
            elif ext in ('.xlsx', '.xls'):
                import pandas as pd
                df = pd.read_excel(data)
                columns_detected = list(df.columns)
                n_rows = len(df)
            else:
                result['status'] = 'error'
                result['issues'].append(f'unsupported file type: {ext}')
        except Exception as e:
            result['status'] = 'error'
            result['issues'].append(f'failed to read: {e}')
    elif isinstance(data, list):
        # 2D list
        n_rows = len(data)
        if data and isinstance(data[0], list):
            # First row might be headers
            n_cols = max(len(r) for r in data)
            # No header info — just generic col_N
            columns_detected = [f'col{i}' for i in range(n_cols)]
    elif isinstance(data, dict):
        columns_detected = list(data.keys())
        n_rows = max((len(v) for v in data.values()), default=0)

    result['columns_detected'] = columns_detected
    result['n_rows'] = n_rows

    # Check required columns
    if tpl['required_columns']:
        cd_lower = [c.lower() for c in columns_detected]
        for req in tpl['required_columns']:
            # Loose match: ignore underscores and case
            req_norm = req.lower().replace('_', '')
            if not any(c.lower().replace('_', '').find(req_norm) >= 0 for c in columns_detected):
                result['issues'].append(f'missing required column: {req}')

    if result['issues']:
        result['status'] = 'warning'

    return result


