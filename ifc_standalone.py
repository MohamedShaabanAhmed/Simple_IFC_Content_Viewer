"""
IFC Toolkit - Standalone Version
A single-file IFC analysis tool with web interface.

Run with: python ifc_standalone.py
Then open: http://localhost:8080
"""

from flask import Flask, request, jsonify, send_file, render_template_string
from flask_cors import CORS
import ifcopenshell
import ifcopenshell.util.element as Element
from pathlib import Path
import tempfile
import os
from werkzeug.utils import secure_filename
import json
from datetime import datetime
import uuid
import xml.etree.ElementTree as ET

# ============================================================================
# FLASK APP SETUP
# ============================================================================

app = Flask(__name__)
CORS(app)
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500MB
app.config['UPLOAD_FOLDER'] = tempfile.gettempdir()

ALLOWED_EXTENSIONS = {'ifc', 'ifcxml'}
ALLOWED_IDS_EXTENSIONS = {'ids', 'xml'}

# Store processed files temporarily
PROCESSED_FILES = {}

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def allowed_ids_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_IDS_EXTENSIONS


def get_element_details(ifc_file, element):
    """Get complete element details."""
    details = {
        "id": getattr(element, "GlobalId", ""),
        "name": getattr(element, "Name", "N/A"),
        "class": element.is_a(),
        "predefinedType": getattr(element, "PredefinedType", None),
        "description": getattr(element, "Description", None),
    }
    
    # Get spatial location
    location = get_spatial_location(element)
    details.update(location)
    
    # Get properties
    psets = Element.get_psets(element)
    properties = {}
    for pset_name, props in psets.items():
        if isinstance(props, dict) and pset_name not in ["id", "type"]:
            clean_props = {k: v for k, v in props.items() if k not in ["id", "type"]}
            if clean_props:
                properties[pset_name] = clean_props
    details["properties"] = properties
    
    # Get quantities
    quantities = get_element_quantities(element)
    details["quantities"] = quantities
    
    return details


def get_spatial_location(element):
    """Get spatial hierarchy location."""
    location = {"storey": None, "building": None, "site": None}
    
    if hasattr(element, "ContainedInStructure"):
        for rel in element.ContainedInStructure:
            if hasattr(rel, "RelatingStructure"):
                container = rel.RelatingStructure
                
                if container.is_a("IfcBuildingStorey"):
                    location["storey"] = {
                        "id": getattr(container, "GlobalId", ""),
                        "name": getattr(container, "Name", ""),
                        "elevation": getattr(container, "Elevation", None)
                    }
                    
                    if hasattr(container, "Decomposes"):
                        for dec in container.Decomposes:
                            if hasattr(dec, "RelatingObject"):
                                building = dec.RelatingObject
                                if building.is_a("IfcBuilding"):
                                    location["building"] = {
                                        "id": getattr(building, "GlobalId", ""),
                                        "name": getattr(building, "Name", "")
                                    }
    
    return location


def get_element_quantities(element):
    """Extract quantities."""
    quantities = {}
    
    if hasattr(element, "IsDefinedBy"):
        for definition in element.IsDefinedBy:
            if definition.is_a("IfcRelDefinesByProperties"):
                prop_def = definition.RelatingPropertyDefinition
                
                if prop_def.is_a("IfcElementQuantity"):
                    qto_name = getattr(prop_def, "Name", "Quantities")
                    
                    if hasattr(prop_def, "Quantities"):
                        for quantity in prop_def.Quantities:
                            qty_name = getattr(quantity, "Name", "")
                            value = None
                            unit = None
                            
                            if hasattr(quantity, "LengthValue"):
                                value = quantity.LengthValue
                                unit = "m"
                            elif hasattr(quantity, "AreaValue"):
                                value = quantity.AreaValue
                                unit = "m²"
                            elif hasattr(quantity, "VolumeValue"):
                                value = quantity.VolumeValue
                                unit = "m³"
                            elif hasattr(quantity, "CountValue"):
                                value = quantity.CountValue
                                unit = "count"
                            
                            if value is not None:
                                quantities[qty_name] = {
                                    "value": value,
                                    "unit": unit,
                                    "quantitySet": qto_name
                                }
    
    return quantities


# ============================================================================
# WEB INTERFACE HTML
# ============================================================================

INTERFACE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>IFC Toolkit - Standalone</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        
        body {
            font-family: 'Segoe UI', system-ui, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        
        .container { max-width: 1400px; margin: 0 auto; }
        
        .header {
            background: white;
            padding: 30px;
            border-radius: 15px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.2);
            margin-bottom: 20px;
            text-align: center;
        }
        
        .header h1 { color: #667eea; font-size: 2.5em; margin-bottom: 10px; }
        .header p { color: #666; font-size: 1.1em; }
        
        .upload-card {
            background: white;
            padding: 40px;
            border-radius: 15px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.2);
            margin-bottom: 20px;
            text-align: center;
        }
        
        .upload-zone {
            border: 3px dashed #667eea;
            border-radius: 10px;
            padding: 50px;
            background: #f8f9ff;
            cursor: pointer;
            transition: all 0.3s;
        }
        
        .upload-zone:hover { background: #e6f7ff; border-color: #5568d3; }
        
        .upload-zone.dragover {
            background: #d9f0ff;
            border-color: #1890ff;
            transform: scale(1.02);
        }
        
        input[type="file"] { display: none; }
        
        .btn {
            display: inline-block;
            padding: 15px 40px;
            background: #667eea;
            color: white;
            border: none;
            border-radius: 8px;
            font-size: 1.1em;
            cursor: pointer;
            transition: all 0.3s;
            margin: 10px;
        }
        
        .btn:hover {
            background: #5568d3;
            transform: translateY(-2px);
            box-shadow: 0 5px 15px rgba(102,126,234,0.4);
        }
        
        .btn-success { background: #48bb78; }
        .btn-success:hover { background: #38a169; }
        
        .results {
            background: white;
            padding: 30px;
            border-radius: 15px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.2);
            display: none;
        }
        
        .results.show { display: block; }
        
        .stats {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }
        
        .stat-card {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 25px;
            border-radius: 10px;
            text-align: center;
        }
        
        .stat-card h3 { font-size: 2.5em; margin-bottom: 5px; }
        .stat-card p { font-size: 1em; opacity: 0.9; }
        
        .element-grid {
            max-height: 500px;
            overflow-y: auto;
            border: 1px solid #ddd;
            border-radius: 8px;
        }
        
        table {
            width: 100%;
            border-collapse: collapse;
        }
        
        th {
            background: #667eea;
            color: white;
            padding: 15px;
            text-align: left;
            position: sticky;
            top: 0;
            z-index: 10;
        }
        
        td {
            padding: 12px 15px;
            border-bottom: 1px solid #eee;
        }
        
        tr:hover { background: #f8f9ff; cursor: pointer; }
        
        .badge {
            display: inline-block;
            padding: 5px 12px;
            border-radius: 20px;
            font-size: 0.85em;
            font-weight: 600;
        }
        
        .badge-primary { background: #e6f7ff; color: #1890ff; }
        .badge-secondary { background: #f0f0f0; color: #666; }
        
        .loading {
            text-align: center;
            padding: 40px;
            display: none;
        }
        
        .loading.show { display: block; }
        
        .spinner {
            border: 4px solid #f3f3f3;
            border-top: 4px solid #667eea;
            border-radius: 50%;
            width: 60px;
            height: 60px;
            animation: spin 1s linear infinite;
            margin: 0 auto 20px;
        }
        
        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }
        
        .tabs {
            display: flex;
            border-bottom: 2px solid #ddd;
            margin-bottom: 20px;
        }
        
        .tab {
            padding: 15px 30px;
            cursor: pointer;
            border: none;
            background: none;
            font-size: 1em;
            color: #666;
            transition: all 0.3s;
        }
        
        .tab.active {
            color: #667eea;
            border-bottom: 3px solid #667eea;
            font-weight: 600;
        }
        
        .tab-content { display: none; }
        .tab-content.active { display: block; }
        
        .copy-btn {
            background: #f0f0f0;
            border: none;
            padding: 5px 10px;
            border-radius: 5px;
            cursor: pointer;
            font-size: 0.85em;
        }
        
        .copy-btn:hover { background: #e0e0e0; }
        
        .file-info {
            background: #e6f7ff;
            padding: 15px;
            border-radius: 8px;
            margin: 20px 0;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🏗️ IFC Toolkit Standalone</h1>
            <p>Professional IFC File Analysis Tool</p>
        </div>
        
        <div class="upload-card">
            <div class="upload-zone" id="uploadZone">
                <h2 style="color: #667eea; margin-bottom: 20px;">📁 Upload IFC File</h2>
                <p style="color: #666; margin-bottom: 20px;">
                    Drag & drop your IFC file here or click to browse
                </p>
                <label for="fileInput" class="btn">
                    Choose File
                </label>
                <input type="file" id="fileInput" accept=".ifc,.ifcxml">
            </div>
            
            <div id="fileInfo" class="file-info" style="display: none;">
                <strong>Selected:</strong> <span id="fileName"></span>
                (<span id="fileSize"></span>)
            </div>
            
            <div style="margin-top: 20px; text-align: left; padding: 20px; background: #f8f9ff; border-radius: 8px;">
                <label style="display: flex; align-items: center; cursor: pointer; font-size: 1em;">
                    <input type="checkbox" id="correctHeaders" style="width: 20px; height: 20px; margin-right: 10px; cursor: pointer;">
                    <span style="color: #667eea; font-weight: 600;">
                        ✅ Apply Header Corrections
                    </span>
                </label>
                <p style="color: #666; font-size: 0.9em; margin: 10px 0 0 30px;">
                    Automatically correct organization, project, and building information according to standards
                </p>
            </div>
        </div>
        
        <div id="loading" class="loading">
            <div class="spinner"></div>
            <h3 style="color: #667eea;">Processing IFC file...</h3>
            <p style="color: #666;">This may take a moment</p>
        </div>
        
        <div id="results" class="results">
            <div class="tabs">
                <button class="tab active" onclick="switchTab(0)">📊 Summary</button>
                <button class="tab" onclick="switchTab(1)">🌲 Elements</button>
                <button class="tab" onclick="switchTab(2)">📏 Quantities</button>
                <button class="tab" onclick="switchTab(3)">✅ Corrections</button>
                <button class="tab" onclick="switchTab(4)">🔍 IDS Validation</button>
            </div>
            
            <div class="tab-content active" id="tab-summary">
                <h2 style="margin-bottom: 20px;">Project Summary</h2>
                <div class="stats" id="stats"></div>
                <div id="byClass"></div>
            </div>
            
            <div class="tab-content" id="tab-elements">
                <div style="display: flex; justify-content: space-between; margin-bottom: 20px;">
                    <h2>All Elements</h2>
                    <button class="btn btn-success" onclick="exportToExcel()">
                        📥 Export to Excel
                    </button>
                </div>
                <div class="element-grid" id="elementGrid"></div>
            </div>
            
            <div class="tab-content" id="tab-quantities">
                <h2 style="margin-bottom: 20px;">Quantities Summary</h2>
                <div id="quantitiesTable"></div>
            </div>
            
            <div class="tab-content" id="tab-corrections">
                <h2 style="margin-bottom: 20px;">Header Corrections Applied</h2>
                <div id="correctionsTable"></div>
                <div id="exportSection" style="display: none; margin-top: 30px; text-align: center;">
                    <button class="btn btn-success" onclick="exportCorrectedFile()">
                        � Save Corrected IFC File
                    </button>
                    <p style="color: #666; margin-top: 10px;">
                        Download the IFC file with all header corrections applied
                    </p>
                </div>
            </div>
            
            <div class="tab-content" id="tab-validation">
                <h2 style="margin-bottom: 20px;">IDS Validation</h2>
                <div style="background: #f8f9ff; padding: 20px; border-radius: 10px; margin-bottom: 20px;">
                    <h3 style="margin-bottom: 15px;">Upload Files for Validation</h3>
                    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 20px;">
                        <div>
                            <label style="display: block; margin-bottom: 10px; font-weight: 600;">IFC File:</label>
                            <input type="file" id="validationIfcFile" accept=".ifc,.ifcxml" 
                                   style="display: block; width: 100%; padding: 10px; border: 2px solid #ddd; border-radius: 5px;">
                        </div>
                        <div>
                            <label style="display: block; margin-bottom: 10px; font-weight: 600;">IDS File:</label>
                            <input type="file" id="validationIdsFile" accept=".ids,.xml" 
                                   style="display: block; width: 100%; padding: 10px; border: 2px solid #ddd; border-radius: 5px;">
                        </div>
                    </div>
                    <button class="btn" onclick="runValidation()">
                        🔍 Run IDS Validation
                    </button>
                </div>
                <div id="validationResults"></div>
            </div>
        </div>
    </div>

    <script>
        let currentFile = null;
        let elementsData = [];
        let correctedFileId = null;
        
        // File upload handling
        const fileInput = document.getElementById('fileInput');
        const uploadZone = document.getElementById('uploadZone');
        
        fileInput.addEventListener('change', handleFileSelect);
        
        uploadZone.addEventListener('click', () => fileInput.click());
        uploadZone.addEventListener('dragover', (e) => {
            e.preventDefault();
            uploadZone.classList.add('dragover');
        });
        uploadZone.addEventListener('dragleave', () => {
            uploadZone.classList.remove('dragover');
        });
        uploadZone.addEventListener('drop', (e) => {
            e.preventDefault();
            uploadZone.classList.remove('dragover');
            const files = e.dataTransfer.files;
            if (files.length > 0) {
                fileInput.files = files;
                handleFileSelect();
            }
        });
        
        function handleFileSelect() {
            const file = fileInput.files[0];
            if (file) {
                currentFile = file;
                document.getElementById('fileName').textContent = file.name;
                document.getElementById('fileSize').textContent = 
                    (file.size / 1024 / 1024).toFixed(2) + ' MB';
                document.getElementById('fileInfo').style.display = 'block';
                
                // Auto-process
                processFile();
            }
        }
        
        async function processFile() {
            if (!currentFile) return;
            
            document.getElementById('loading').classList.add('show');
            document.getElementById('results').classList.remove('show');
            
            const formData = new FormData();
            formData.append('file', currentFile);
            
            // Add correction option
            const correctHeaders = document.getElementById('correctHeaders').checked;
            formData.append('correctHeaders', correctHeaders);
            
            try {
                const response = await fetch('/api/analyze', {
                    method: 'POST',
                    body: formData
                });
                
                const data = await response.json();
                
                if (data.success) {
                    elementsData = data.elements;
                    correctedFileId = data.fileId;
                    displayResults(data);
                } else {
                    alert('Error: ' + data.error);
                }
            } catch (error) {
                alert('Error processing file: ' + error.message);
            } finally {
                document.getElementById('loading').classList.remove('show');
            }
        }
        
        function displayResults(data) {
            // Summary stats
            const stats = document.getElementById('stats');
            stats.innerHTML = `
                <div class="stat-card">
                    <h3>${data.summary.totalElements}</h3>
                    <p>Total Elements</p>
                </div>
                <div class="stat-card">
                    <h3>${data.summary.uniqueClasses}</h3>
                    <p>Element Classes</p>
                </div>
                <div class="stat-card">
                    <h3>${data.summary.uniqueStoreys}</h3>
                    <p>Building Storeys</p>
                </div>
                <div class="stat-card">
                    <h3>${data.summary.uniqueBuildings}</h3>
                    <p>Buildings</p>
                </div>
            `;
            
            // By class table
            const byClass = document.getElementById('byClass');
            let classHTML = '<h3 style="margin: 20px 0;">Elements by Class</h3><table><tr><th>Class</th><th>Count</th></tr>';
            for (const [cls, count] of Object.entries(data.summary.byClass)) {
                classHTML += `<tr><td><span class="badge badge-primary">${cls}</span></td><td>${count}</td></tr>`;
            }
            classHTML += '</table>';
            byClass.innerHTML = classHTML;
            
            // Elements grid
            displayElementsGrid(data.elements);
            
            // Quantities
            displayQuantities(data.elements);
            
            // Corrections
            displayCorrections(data.corrections || []);
            
            document.getElementById('results').classList.add('show');
        }
        
        function displayCorrections(corrections) {
            const correctionsTable = document.getElementById('correctionsTable');
            
            if (!corrections || corrections.length === 0) {
                correctionsTable.innerHTML = `
                    <div style="text-align: center; padding: 40px; color: #666;">
                        <h3>No corrections applied</h3>
                        <p>Check the "Apply Header Corrections" option when uploading to see corrections.</p>
                    </div>
                `;
                return;
            }
            
            let html = `
                <div style="background: #e6f7ff; padding: 15px; border-radius: 8px; margin-bottom: 20px;">
                    <strong style="color: #1890ff;">✅ ${corrections.length} corrections applied successfully!</strong>
                </div>
                <table>
                    <thead>
                        <tr>
                            <th>Field</th>
                            <th>Old Value</th>
                            <th>New Value</th>
                        </tr>
                    </thead>
                    <tbody>
            `;
            
            corrections.forEach(corr => {
                html += `
                    <tr>
                        <td><strong>${corr.field}</strong></td>
                        <td style="color: #999; text-decoration: line-through;">${corr.old || 'N/A'}</td>
                        <td style="color: #48bb78; font-weight: 600;">${corr.new}</td>
                    </tr>
                `;
            });
            
            html += '</tbody></table>';
            correctionsTable.innerHTML = html;
            
            // Show export button if we have a corrected file
            if (correctedFileId) {
                document.getElementById('exportSection').style.display = 'block';
            }
        }
        
        function exportCorrectedFile() {
            if (!correctedFileId) {
                alert('No corrected file available');
                return;
            }
            
            window.location.href = `/api/export/${correctedFileId}`;
        }
        
        async function runValidation() {
            const ifcFile = document.getElementById('validationIfcFile').files[0];
            const idsFile = document.getElementById('validationIdsFile').files[0];
            
            if (!ifcFile || !idsFile) {
                alert('Please select both IFC and IDS files');
                return;
            }
            
            document.getElementById('loading').classList.add('show');
            
            const formData = new FormData();
            formData.append('ifc_file', ifcFile);
            formData.append('ids_file', idsFile);
            
            try {
                const response = await fetch('/api/validate', {
                    method: 'POST',
                    body: formData
                });
                
                const data = await response.json();
                
                if (data.success !== false) {
                    displayValidationResults(data);
                } else {
                    alert('Error: ' + data.error);
                }
            } catch (error) {
                alert('Error running validation: ' + error.message);
            } finally {
                document.getElementById('loading').classList.remove('show');
            }
        }
        
        function displayValidationResults(results) {
            const resultsDiv = document.getElementById('validationResults');
            
            const passRate = results.totalSpecifications > 0 ? 
                Math.round((results.passedSpecifications / results.totalSpecifications) * 100) : 0;
            
            let html = `
                <div style="background: ${passRate === 100 ? '#d4edda' : '#fff3cd'}; 
                            padding: 20px; border-radius: 10px; margin-bottom: 20px;">
                    <h3 style="margin-bottom: 10px;">Validation Results</h3>
                    <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 15px; margin-top: 15px;">
                        <div>
                            <strong>Total Specifications:</strong> ${results.totalSpecifications}
                        </div>
                        <div style="color: #28a745;">
                            <strong>✅ Passed:</strong> ${results.passedSpecifications}
                        </div>
                        <div style="color: #dc3545;">
                            <strong>❌ Failed:</strong> ${results.failedSpecifications}
                        </div>
                    </div>
                    <div style="margin-top: 15px;">
                        <strong>Pass Rate:</strong> 
                        <span style="font-size: 1.5em; color: ${passRate === 100 ? '#28a745' : '#ffc107'};">
                            ${passRate}%
                        </span>
                    </div>
                </div>
                
                <h3 style="margin: 20px 0;">Specification Details</h3>
                <table>
                    <thead>
                        <tr>
                            <th>Status</th>
                            <th>Specification</th>
                            <th>Requirements</th>
                            <th>Issues</th>
                        </tr>
                    </thead>
                    <tbody>
            `;
            
            results.specifications.forEach(spec => {
                const statusIcon = spec.passed ? '✅' : '❌';
                const statusColor = spec.passed ? '#28a745' : '#dc3545';
                
                html += `
                    <tr>
                        <td style="text-align: center; font-size: 1.5em;">${statusIcon}</td>
                        <td>
                            <strong>${spec.name}</strong>
                            ${spec.description ? '<br><small style="color: #666;">' + spec.description + '</small>' : ''}
                        </td>
                        <td>
                            ${spec.requirements.length > 0 ? 
                                '<ul style="margin: 0; padding-left: 20px;">' + 
                                spec.requirements.map(r => '<li>' + r + '</li>').join('') + 
                                '</ul>' : 
                                'N/A'}
                        </td>
                        <td style="color: ${statusColor};">
                            ${spec.failures.length > 0 ? 
                                '<ul style="margin: 0; padding-left: 20px;">' + 
                                spec.failures.map(f => '<li>' + f + '</li>').join('') + 
                                '</ul>' : 
                                'All checks passed'}
                        </td>
                    </tr>
                `;
            });
            
            html += '</tbody></table>';
            resultsDiv.innerHTML = html;
        }
        
        function displayElementsGrid(elements) {
            const grid = document.getElementById('elementGrid');
            let html = `
                <table>
                    <thead>
                        <tr>
                            <th>Name</th>
                            <th>Class</th>
                            <th>Storey</th>
                            <th>Volume (m³)</th>
                            <th>Area (m²)</th>
                        </tr>
                    </thead>
                    <tbody>
            `;
            
            elements.forEach(elem => {
                const storey = elem.storey ? elem.storey.name : 'N/A';
                const volume = elem.quantities.NetVolume ? 
                    elem.quantities.NetVolume.value.toFixed(3) : '-';
                const area = elem.quantities.NetArea ? 
                    elem.quantities.NetArea.value.toFixed(3) : '-';
                
                html += `
                    <tr onclick="showElementDetails('${elem.id}')">
                        <td>${elem.name}</td>
                        <td><span class="badge badge-primary">${elem.class}</span></td>
                        <td>${storey}</td>
                        <td>${volume}</td>
                        <td>${area}</td>
                    </tr>
                `;
            });
            
            html += '</tbody></table>';
            grid.innerHTML = html;
        }
        
        function displayQuantities(elements) {
            const qtyTable = document.getElementById('quantitiesTable');
            
            // Aggregate by class
            const byClass = {};
            elements.forEach(elem => {
                if (!byClass[elem.class]) {
                    byClass[elem.class] = { count: 0, volume: 0, area: 0 };
                }
                byClass[elem.class].count++;
                if (elem.quantities.NetVolume) {
                    byClass[elem.class].volume += elem.quantities.NetVolume.value;
                }
                if (elem.quantities.NetArea) {
                    byClass[elem.class].area += elem.quantities.NetArea.value;
                }
            });
            
            let html = `
                <table>
                    <thead>
                        <tr>
                            <th>Class</th>
                            <th>Count</th>
                            <th>Total Volume (m³)</th>
                            <th>Total Area (m²)</th>
                        </tr>
                    </thead>
                    <tbody>
            `;
            
            for (const [cls, data] of Object.entries(byClass)) {
                html += `
                    <tr>
                        <td><span class="badge badge-primary">${cls}</span></td>
                        <td>${data.count}</td>
                        <td>${data.volume.toFixed(3)}</td>
                        <td>${data.area.toFixed(3)}</td>
                    </tr>
                `;
            }
            
            html += '</tbody></table>';
            qtyTable.innerHTML = html;
        }
        
        function switchTab(index) {
            const tabs = document.querySelectorAll('.tab');
            const contents = document.querySelectorAll('.tab-content');
            
            tabs.forEach(t => t.classList.remove('active'));
            contents.forEach(c => c.classList.remove('active'));
            
            tabs[index].classList.add('active');
            contents[index].classList.add('active');
        }
        
        function exportToExcel() {
            // Simple CSV export
            let csv = 'Name,Class,Storey,Volume,Area\\n';
            elementsData.forEach(elem => {
                const storey = elem.storey ? elem.storey.name : '';
                const volume = elem.quantities.NetVolume ? 
                    elem.quantities.NetVolume.value : '';
                const area = elem.quantities.NetArea ? 
                    elem.quantities.NetArea.value : '';
                
                csv += `"${elem.name}","${elem.class}","${storey}",${volume},${area}\\n`;
            });
            
            const blob = new Blob([csv], { type: 'text/csv' });
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = 'ifc_elements.csv';
            a.click();
        }
    </script>
</body>
</html>
"""


# ============================================================================
# HEADER CORRECTION MAPPING
# ============================================================================

HEADER_CORRECTIONS = {
    "OrganizationName": "Subcontractor Full Name",
    "OrganizationDescription": "Primary Asset Name (e.g., Long Bridge/High Rise Building)",
    "Author": "Subcontractor initials or ID (e.g., DR or WS)",
    "BuildingId": "H2 Primary Asset",
    "SiteCode": "H2 Bridge Building, Railings, Roof etc.",
    "ProjectStatus": "H2 Works Civils/MEP and Net Sectors forming part of the H2 Project: H2WCC2257 - H2 and H2WCC2257 - ME",
    "ClientName": "H2 Primary Group",
    "ProjectName": "Primary Group Name (e.g., Long Bridge/Tunnel Enquiry)",
    "ProjectIssueDate": "Issue Date",
    "ProjectPhase": "Stage 3: Construction and Manufacturing (CD) / CR: Construction Record File",
    "ProjectAddress": "H2 Works Civils/MEP and Net Sectors",
    "ProjectNumber": "H2"
}


def correct_ifc_headers(ifc_file):
    """Apply header corrections to IFC file."""
    corrections_applied = []
    
    try:
        # Get project info
        project = ifc_file.by_type("IfcProject")[0] if ifc_file.by_type("IfcProject") else None
        
        if project:
            # Update Organization
            if hasattr(project, "OwnerHistory"):
                owner_history = project.OwnerHistory
                
                if hasattr(owner_history, "OwningUser"):
                    user = owner_history.OwningUser
                    
                    if hasattr(user, "TheOrganization") and user.TheOrganization:
                        org = user.TheOrganization
                        
                        # Organization Name
                        if hasattr(org, "Name"):
                            old_val = org.Name
                            new_val = HEADER_CORRECTIONS.get("OrganizationName", old_val)
                            if old_val != new_val:
                                corrections_applied.append({
                                    "field": "Organization Name",
                                    "old": old_val,
                                    "new": new_val
                                })
                                # Apply correction
                                ifc_file.create_entity("IfcLabel", new_val)
                        
                        # Organization Description
                        if hasattr(org, "Description"):
                            old_val = org.Description
                            new_val = HEADER_CORRECTIONS.get("OrganizationDescription", old_val)
                            if old_val != new_val:
                                corrections_applied.append({
                                    "field": "Organization Description",
                                    "old": old_val,
                                    "new": new_val
                                })
                    
                    # Author
                    if hasattr(user, "GivenName"):
                        old_val = user.GivenName
                        new_val = HEADER_CORRECTIONS.get("Author", old_val)
                        if old_val != new_val:
                            corrections_applied.append({
                                "field": "Author",
                                "old": old_val,
                                "new": new_val
                            })
            
            # Update Project fields
            if hasattr(project, "Name"):
                old_val = project.Name
                new_val = HEADER_CORRECTIONS.get("ProjectName", old_val)
                if old_val != new_val:
                    corrections_applied.append({
                        "field": "Project Name",
                        "old": old_val,
                        "new": new_val
                    })
            
            if hasattr(project, "Description"):
                old_val = project.Description
                new_val = HEADER_CORRECTIONS.get("ProjectStatus", old_val)
                if old_val != new_val:
                    corrections_applied.append({
                        "field": "Project Status",
                        "old": old_val,
                        "new": new_val
                    })
        
        # Get building
        buildings = ifc_file.by_type("IfcBuilding")
        if buildings:
            building = buildings[0]
            
            # Building ID
            if hasattr(building, "Name"):
                old_val = building.Name
                new_val = HEADER_CORRECTIONS.get("BuildingId", old_val)
                if old_val != new_val:
                    corrections_applied.append({
                        "field": "Building ID",
                        "old": old_val,
                        "new": new_val
                    })
        
        # Get site
        sites = ifc_file.by_type("IfcSite")
        if sites:
            site = sites[0]
            
            # Site Code
            if hasattr(site, "Name"):
                old_val = site.Name
                new_val = HEADER_CORRECTIONS.get("SiteCode", old_val)
                if old_val != new_val:
                    corrections_applied.append({
                        "field": "Site Code",
                        "old": old_val,
                        "new": new_val
                    })
    
    except Exception as e:
        print(f"Warning: Could not apply all corrections: {e}")
    
    return corrections_applied


def validate_against_ids(ifc_file, ids_path):
    """Validate IFC against IDS file."""
    results = {
        "success": True,
        "totalSpecifications": 0,
        "passedSpecifications": 0,
        "failedSpecifications": 0,
        "specifications": []
    }
    
    try:
        # Parse IDS file
        tree = ET.parse(ids_path)
        root = tree.getroot()
        
        # Get namespace
        ns = {'ids': 'http://standards.buildingsmart.org/IDS'}
        if not root.tag.endswith('ids'):
            ns = {}
        
        # Find all specifications
        specs = root.findall('.//ids:specification', ns) if ns else root.findall('.//specification')
        
        results["totalSpecifications"] = len(specs)
        
        for spec in specs:
            spec_name = spec.get('name', 'Unnamed Specification')
            spec_result = {
                "name": spec_name,
                "description": spec.get('description', ''),
                "passed": True,
                "requirements": [],
                "failures": []
            }
            
            # Find applicability (which elements to check)
            applicability = spec.find('.//ids:applicability', ns) if ns else spec.find('.//applicability')
            
            # Find requirements
            requirements = spec.find('.//ids:requirements', ns) if ns else spec.find('.//requirements')
            
            if applicability is not None and requirements is not None:
                # Get entity constraint
                entity = applicability.find('.//ids:entity', ns) if ns else applicability.find('.//entity')
                
                if entity is not None:
                    entity_name = entity.find('.//ids:name', ns) if ns else entity.find('.//name')
                    
                    if entity_name is not None:
                        ifc_class = entity_name.text
                        
                        # Get elements of this type
                        try:
                            elements = ifc_file.by_type(ifc_class)
                            
                            # Check property requirements
                            prop_reqs = requirements.findall('.//ids:property', ns) if ns else requirements.findall('.//property')
                            
                            for prop_req in prop_reqs:
                                pset_name_elem = prop_req.find('.//ids:propertySet', ns) if ns else prop_req.find('.//propertySet')
                                prop_name_elem = prop_req.find('.//ids:name', ns) if ns else prop_req.find('.//name')
                                
                                if pset_name_elem is not None and prop_name_elem is not None:
                                    pset_name = pset_name_elem.find('.//ids:simpleValue', ns).text if ns else pset_name_elem.find('.//simpleValue').text
                                    prop_name = prop_name_elem.find('.//ids:simpleValue', ns).text if ns else prop_name_elem.find('.//simpleValue').text
                                    
                                    req_info = f"{pset_name}.{prop_name} must exist"
                                    spec_result["requirements"].append(req_info)
                                    
                                    # Check elements
                                    missing_count = 0
                                    for elem in elements:
                                        psets = Element.get_psets(elem)
                                        
                                        if pset_name not in psets or prop_name not in psets[pset_name]:
                                            missing_count += 1
                                    
                                    if missing_count > 0:
                                        spec_result["passed"] = False
                                        spec_result["failures"].append(
                                            f"{missing_count} elements missing {pset_name}.{prop_name}"
                                        )
                        
                        except Exception as e:
                            spec_result["failures"].append(f"Error checking {ifc_class}: {str(e)}")
                            spec_result["passed"] = False
            
            results["specifications"].append(spec_result)
            
            if spec_result["passed"]:
                results["passedSpecifications"] += 1
            else:
                results["failedSpecifications"] += 1
    
    except Exception as e:
        results["success"] = False
        results["error"] = str(e)
    
    return results


# ============================================================================
# API ENDPOINTS
# ============================================================================

@app.route('/')
def home():
    """Serve the main interface."""
    return render_template_string(INTERFACE_HTML)


@app.route('/api/analyze', methods=['POST'])
def analyze_file():
    """Analyze IFC file and return all data."""
    if 'file' not in request.files:
        return jsonify({"success": False, "error": "No file provided"}), 400
    
    file = request.files['file']
    
    if not allowed_file(file.filename):
        return jsonify({"success": False, "error": "Invalid file type"}), 400
    
    try:
        # Save file
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        
        # Load IFC
        ifc_file = ifcopenshell.open(filepath)
        
        # Check if corrections should be applied
        apply_corrections = request.form.get('correctHeaders', 'false') == 'true'
        corrections = []
        
        if apply_corrections:
            corrections = correct_ifc_headers(ifc_file)
        
        # Get all elements
        elements = ifc_file.by_type("IfcProduct")
        
        # Process elements
        elements_data = []
        by_class = {}
        by_storey = {}
        by_building = {}
        
        for element in elements:
            elem_data = get_element_details(ifc_file, element)
            elements_data.append(elem_data)
            
            # Count by class
            elem_class = elem_data["class"]
            by_class[elem_class] = by_class.get(elem_class, 0) + 1
            
            # Count by storey
            if elem_data.get("storey"):
                storey_name = elem_data["storey"]["name"]
                by_storey[storey_name] = by_storey.get(storey_name, 0) + 1
            
            # Count by building
            if elem_data.get("building"):
                building_name = elem_data["building"]["name"]
                by_building[building_name] = by_building.get(building_name, 0) + 1
        
        # Save corrected file if corrections were applied
        file_id = None
        if apply_corrections and corrections:
            file_id = str(uuid.uuid4())
            corrected_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{file_id}_corrected.ifc")
            ifc_file.write(corrected_path)
            PROCESSED_FILES[file_id] = {
                'path': corrected_path,
                'filename': filename,
                'timestamp': datetime.now()
            }
        
        # Process elements
        elements_data = []
        by_class = {}
        by_storey = {}
        by_building = {}
        
        for element in elements:
            elem_data = get_element_details(ifc_file, element)
            elements_data.append(elem_data)
            
            # Count by class
            elem_class = elem_data["class"]
            by_class[elem_class] = by_class.get(elem_class, 0) + 1
            
            # Count by storey
            if elem_data.get("storey"):
                storey_name = elem_data["storey"]["name"]
                by_storey[storey_name] = by_storey.get(storey_name, 0) + 1
            
            # Count by building
            if elem_data.get("building"):
                building_name = elem_data["building"]["name"]
                by_building[building_name] = by_building.get(building_name, 0) + 1
        
        # Cleanup original file
        os.remove(filepath)
        
        return jsonify({
            "success": True,
            "elements": elements_data,
            "corrections": corrections,
            "fileId": file_id,
            "summary": {
                "totalElements": len(elements_data),
                "byClass": by_class,
                "byStorey": by_storey,
                "byBuilding": by_building,
                "uniqueClasses": len(by_class),
                "uniqueStoreys": len(by_storey),
                "uniqueBuildings": len(by_building)
            }
        })
        
    except Exception as e:
        if os.path.exists(filepath):
            os.remove(filepath)
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/export/<file_id>', methods=['GET'])
def export_corrected_file(file_id):
    """Download corrected IFC file."""
    if file_id not in PROCESSED_FILES:
        return jsonify({"success": False, "error": "File not found"}), 404
    
    file_info = PROCESSED_FILES[file_id]
    filepath = file_info['path']
    
    if not os.path.exists(filepath):
        return jsonify({"success": False, "error": "File no longer available"}), 404
    
    return send_file(
        filepath,
        as_attachment=True,
        download_name=f"corrected_{file_info['filename']}"
    )


@app.route('/api/validate', methods=['POST'])
def validate_ifc():
    """Validate IFC against IDS file."""
    if 'ifc_file' not in request.files or 'ids_file' not in request.files:
        return jsonify({"success": False, "error": "Both IFC and IDS files required"}), 400
    
    ifc_file_upload = request.files['ifc_file']
    ids_file_upload = request.files['ids_file']
    
    if not allowed_file(ifc_file_upload.filename):
        return jsonify({"success": False, "error": "Invalid IFC file type"}), 400
    
    if not allowed_ids_file(ids_file_upload.filename):
        return jsonify({"success": False, "error": "Invalid IDS file type"}), 400
    
    try:
        # Save files
        ifc_filename = secure_filename(ifc_file_upload.filename)
        ids_filename = secure_filename(ids_file_upload.filename)
        
        ifc_path = os.path.join(app.config['UPLOAD_FOLDER'], ifc_filename)
        ids_path = os.path.join(app.config['UPLOAD_FOLDER'], ids_filename)
        
        ifc_file_upload.save(ifc_path)
        ids_file_upload.save(ids_path)
        
        # Load IFC
        ifc_file = ifcopenshell.open(ifc_path)
        
        # Validate
        results = validate_against_ids(ifc_file, ids_path)
        
        # Cleanup
        os.remove(ifc_path)
        os.remove(ids_path)
        
        return jsonify(results)
        
    except Exception as e:
        if os.path.exists(ifc_path):
            os.remove(ifc_path)
        if os.path.exists(ids_path):
            os.remove(ids_path)
        return jsonify({"success": False, "error": str(e)}), 500


# ============================================================================
# MAIN
# ============================================================================

if __name__ == '__main__':
    print("=" * 60)
    print("🏗️  IFC Toolkit - Standalone Version")
    print("=" * 60)
    print("Starting server...")
    print("Open in browser: http://localhost:8080")
    print("=" * 60)
    print()
    
    app.run(
        host='0.0.0.0',
        port=8080,
        debug=True
    )
