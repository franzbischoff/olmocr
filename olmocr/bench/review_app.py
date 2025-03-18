#!/usr/bin/env python3
import argparse
import json
import os
import shutil
import sys
import tempfile
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

import flask
from flask import Flask, render_template, request, jsonify, redirect, url_for, send_file
from werkzeug.utils import secure_filename

from olmocr.data.renderpdf import render_pdf_to_base64png
from . import tests

app = Flask(__name__)

# Global state
DATASET_DIR = ""
CURRENT_PDF = None
PDF_TESTS = {}
ALL_PDFS = []


def find_next_unchecked_pdf() -> Optional[str]:
    """Find the next PDF with at least one unchecked test."""
    global PDF_TESTS, ALL_PDFS
    
    for pdf_name in ALL_PDFS:
        pdf_tests = PDF_TESTS[pdf_name]
        for test in pdf_tests:
            if test.get("checked") is None:
                return pdf_name
    return None


def save_dataset(jsonl_file: str) -> None:
    """Save the tests to a JSONL file, using temp file for atomic write."""
    global PDF_TESTS
    
    # Flatten all tests
    all_tests = []
    for pdf_tests in PDF_TESTS.values():
        all_tests.extend(pdf_tests)
    
    # Create temp file and write updated content
    with tempfile.NamedTemporaryFile(mode='w', delete=False) as temp_file:
        for test in all_tests:
            temp_file.write(json.dumps(test) + "\n")
    
    # Atomic replace
    shutil.move(temp_file.name, jsonl_file)


@app.route('/pdf/<path:pdf_name>')
def serve_pdf(pdf_name):
    """Serve the PDF file directly."""
    pdf_path = os.path.join(DATASET_DIR, "pdfs", pdf_name)
    return send_file(pdf_path, mimetype='application/pdf')


@app.route('/')
def index():
    """Main page displaying the current PDF and its tests."""
    global CURRENT_PDF, PDF_TESTS, DATASET_DIR
    
    # If no current PDF is set, find the next one with unchecked tests
    if CURRENT_PDF is None:
        CURRENT_PDF = find_next_unchecked_pdf()
    
    # If still no PDF, all tests have been checked
    if CURRENT_PDF is None:
        return render_template('all_done.html')
    
    # Get the tests for the current PDF
    current_tests = PDF_TESTS.get(CURRENT_PDF, [])
    
    # Create PDF URL for pdf.js to load
    pdf_url = url_for('serve_pdf', pdf_name=CURRENT_PDF)
    
    return render_template(
        'review.html', 
        pdf_name=CURRENT_PDF,
        tests=current_tests,
        pdf_path=pdf_url,
        pdf_index=ALL_PDFS.index(CURRENT_PDF) if CURRENT_PDF in ALL_PDFS else 0,
        total_pdfs=len(ALL_PDFS)
    )


@app.route('/update_test', methods=['POST'])
def update_test():
    """API endpoint to update a test."""
    global PDF_TESTS, DATASET_DIR
    
    data = request.json
    pdf_name = data.get('pdf')
    test_id = data.get('id')
    field = data.get('field')
    value = data.get('value')
    
    # Find and update the test
    for test in PDF_TESTS.get(pdf_name, []):
        if test.get('id') == test_id:
            test[field] = value
            break
    
    # Save the updated tests
    dataset_file = os.path.join(DATASET_DIR, "table_tests.jsonl")
    save_dataset(dataset_file)
    
    return jsonify({"status": "success"})


@app.route('/next_pdf', methods=['POST'])
def next_pdf():
    """Move to the next PDF in the list."""
    global CURRENT_PDF, ALL_PDFS
    
    if CURRENT_PDF in ALL_PDFS:
        current_index = ALL_PDFS.index(CURRENT_PDF)
        if current_index < len(ALL_PDFS) - 1:
            CURRENT_PDF = ALL_PDFS[current_index + 1]
        else:
            CURRENT_PDF = find_next_unchecked_pdf()
    else:
        CURRENT_PDF = find_next_unchecked_pdf()
    
    return redirect(url_for('index'))


@app.route('/prev_pdf', methods=['POST'])
def prev_pdf():
    """Move to the previous PDF in the list."""
    global CURRENT_PDF, ALL_PDFS
    
    if CURRENT_PDF in ALL_PDFS:
        current_index = ALL_PDFS.index(CURRENT_PDF)
        if current_index > 0:
            CURRENT_PDF = ALL_PDFS[current_index - 1]
    
    return redirect(url_for('index'))


@app.route('/goto_pdf/<int:index>', methods=['POST'])
def goto_pdf(index):
    """Go to a specific PDF by index."""
    global CURRENT_PDF, ALL_PDFS
    
    if 0 <= index < len(ALL_PDFS):
        CURRENT_PDF = ALL_PDFS[index]
    
    return redirect(url_for('index'))


def load_dataset(dataset_dir: str) -> Tuple[Dict[str, List[Dict]], List[str]]:
    """Load tests from the dataset file and organize them by PDF."""
    dataset_file = os.path.join(dataset_dir, "table_tests.jsonl")
    
    if not os.path.exists(dataset_file):
        raise FileNotFoundError(f"Dataset file not found: {dataset_file}")
    
    pdf_tests = defaultdict(list)
    
    with open(dataset_file, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
                
            try:
                test = json.loads(line)
                pdf_name = test.get('pdf')
                if pdf_name:
                    pdf_tests[pdf_name].append(test)
            except json.JSONDecodeError:
                print(f"Warning: Could not parse line as JSON: {line}")
    
    all_pdfs = list(pdf_tests.keys())
    
    return pdf_tests, all_pdfs


def create_templates_directory():
    """Create templates directory for Flask if it doesn't exist."""
    templates_dir = os.path.join(os.path.dirname(__file__), 'templates')
    os.makedirs(templates_dir, exist_ok=True)
    
    # Create review template
    review_template = os.path.join(templates_dir, 'review.html')
    with open(review_template, 'w') as f:
        f.write("""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PDF Test Review</title>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js"></script>
    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 0;
            padding: 20px;
            background-color: #f5f5f5;
        }
        
        .container {
            max-width: 1920px;
            margin: 0 auto;
            display: flex;
            flex-direction: row;
        }
        
        h1 {
            color: #333;
            margin-bottom: 20px;
        }
        
        .navigation {
            display: flex;
            justify-content: space-between;
            margin-bottom: 20px;
        }
        
        .pdf-viewer {
            flex: 1;
            padding: 20px;
            background-color: white;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0, 0, 0, 0.1);
            margin-right: 20px;
            overflow: auto;
            max-height: calc(100vh - 100px);
            display: flex;
            flex-direction: column;
        }
        
        #pdf-container {
            width: 100%;
            flex-grow: 1;
            border: 1px solid #ddd;
            overflow: auto;
            position: relative;
        }
        
        .pdf-controls {
            display: flex;
            justify-content: center;
            align-items: center;
            margin-bottom: 10px;
            gap: 10px;
        }
        
        .pdf-controls button {
            padding: 5px 10px;
            border: 1px solid #ccc;
            background-color: #f5f5f5;
            border-radius: 4px;
            cursor: pointer;
        }
        
        .pdf-controls span {
            margin: 0 10px;
        }
        
        .pdf-canvas {
            display: block;
            margin: 0 auto;
        }
        
        .tests-panel {
            flex: 1;
            padding: 20px;
            background-color: white;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0, 0, 0, 0.1);
            overflow-y: auto;
            max-height: calc(100vh - 100px);
        }
        
        .test-item {
            margin-bottom: 20px;
            padding: 15px;
            border: 1px solid #e0e0e0;
            border-radius: 4px;
        }
        
        .test-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 10px;
        }
        
        .test-type {
            display: inline-block;
            padding: 5px 10px;
            border-radius: 4px;
            color: white;
            font-weight: bold;
        }
        
        .present {
            background-color: #28a745;
        }
        
        .absent {
            background-color: #dc3545;
        }
        
        .order {
            background-color: #fd7e14;
        }
        
        .table {
            background-color: #17a2b8;
        }
        
        .math {
            background-color: #6f42c1;
        }
        
        .baseline {
            background-color: #4a6fa5;
        }
        
        .unknown {
            background-color: #6c757d;
        }
        
        .test-buttons {
            display: flex;
            gap: 10px;
        }
        
        .test-content {
            margin-bottom: 10px;
        }
        
        button {
            padding: 8px 16px;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-weight: bold;
        }
        
        .approve-btn {
            background-color: #28a745;
            color: white;
        }
        
        .reject-btn {
            background-color: #dc3545;
            color: white;
        }
        
        .edit-btn {
            background-color: #17a2b8;
            color: white;
        }
        
        .highlight-btn {
            background-color: #ffc107;
            color: #333;
        }
        
        .next-btn, .prev-btn {
            background-color: #4a6fa5;
            color: white;
        }
        
        textarea {
            width: 100%;
            padding: 8px;
            border: 1px solid #ccc;
            border-radius: 4px;
            resize: vertical;
        }
        
        .editable {
            border: 1px dashed #ccc;
            padding: 5px;
            margin-bottom: 5px;
        }
        
        .status-approved {
            border-left: 5px solid #28a745;
        }
        
        .status-rejected {
            border-left: 5px solid #dc3545;
        }
        
        /* PDF.js text layer styles */
        .textLayer {
            position: absolute;
            left: 0;
            top: 0;
            right: 0;
            bottom: 0;
            overflow: hidden;
            opacity: 0.25;
            text-align: initial;
            line-height: 1.0;
            pointer-events: none;
        }
        
        .textLayer span {
            color: transparent;
            position: absolute;
            white-space: pre;
            cursor: text;
            transform-origin: 0% 0%;
            pointer-events: all;
        }
        
        .textLayer .highlight {
            background-color: rgba(255, 255, 0, 0.5);
            border-radius: 3px;
        }
        
        .loading-indicator {
            display: none;
            position: absolute;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            background-color: rgba(0, 0, 0, 0.7);
            color: white;
            padding: 20px;
            border-radius: 5px;
            font-weight: bold;
        }
    </style>
</head>
<body>
    <h1>PDF Test Review: {{ pdf_name }} ({{ pdf_index + 1 }}/{{ total_pdfs }})</h1>
    
    <div class="navigation">
        <form action="/prev_pdf" method="post">
            <button type="submit" class="prev-btn">Previous PDF</button>
        </form>
        <form action="/next_pdf" method="post">
            <button type="submit" class="next-btn">Next PDF</button>
        </form>
    </div>
    
    <div class="container">
        <div class="pdf-viewer">
            <div class="pdf-controls">
                <button id="prev-page">Previous Page</button>
                <span id="page-num"></span> / <span id="page-count"></span>
                <button id="next-page">Next Page</button>
                <button id="zoom-in">Zoom In</button>
                <button id="zoom-out">Zoom Out</button>
            </div>
            <div id="pdf-container">
                <canvas id="pdf-canvas" class="pdf-canvas"></canvas>
                <div id="text-layer" class="textLayer"></div>
                <div class="loading-indicator" id="loading-indicator">Loading PDF...</div>
            </div>
        </div>
        
        <div class="tests-panel">
            <h2>Tests ({{ tests|length }})</h2>
            
            {% for test in tests %}
            <div class="test-item {% if test.checked == 'verified' %}status-approved{% elif test.checked == 'rejected' %}status-rejected{% endif %}" data-id="{{ test.id }}">
                <div class="test-header">
                    <span class="test-type {{ test.type }}">{{ test.type|upper }}</span>
                    <div class="test-buttons">
                        <button class="approve-btn" onclick="updateTestStatus('{{ test.pdf }}', '{{ test.id }}', 'checked', 'verified')">Approve</button>
                        <button class="reject-btn" onclick="updateTestStatus('{{ test.pdf }}', '{{ test.id }}', 'checked', 'rejected')">Reject</button>
                        <button class="edit-btn" onclick="toggleEditMode('{{ test.id }}')">Edit</button>
                        <button class="highlight-btn" onclick="highlightText('{{ test.id }}')">Highlight</button>
                    </div>
                </div>
                
                <div class="test-content">
                    {% if test.type == 'present' or test.type == 'absent' %}
                        <div><strong>Text:</strong> <span class="editable" data-field="text" data-id="{{ test.id }}">{{ test.text }}</span></div>
                        <div><strong>Case Sensitive:</strong> {{ test.case_sensitive }}</div>
                        {% if test.first_n %}<div><strong>First N:</strong> {{ test.first_n }}</div>{% endif %}
                        {% if test.last_n %}<div><strong>Last N:</strong> {{ test.last_n }}</div>{% endif %}
                    {% elif test.type == 'order' %}
                        <div><strong>Before:</strong> <span class="editable" data-field="before" data-id="{{ test.id }}">{{ test.before }}</span></div>
                        <div><strong>After:</strong> <span class="editable" data-field="after" data-id="{{ test.id }}">{{ test.after }}</span></div>
                    {% elif test.type == 'table' %}
                        <div><strong>Cell:</strong> <span class="editable" data-field="cell" data-id="{{ test.id }}">{{ test.cell }}</span></div>
                        {% if test.up %}<div><strong>Up:</strong> <span class="editable" data-field="up" data-id="{{ test.id }}">{{ test.up }}</span></div>{% endif %}
                        {% if test.down %}<div><strong>Down:</strong> <span class="editable" data-field="down" data-id="{{ test.id }}">{{ test.down }}</span></div>{% endif %}
                        {% if test.left %}<div><strong>Left:</strong> <span class="editable" data-field="left" data-id="{{ test.id }}">{{ test.left }}</span></div>{% endif %}
                        {% if test.right %}<div><strong>Right:</strong> <span class="editable" data-field="right" data-id="{{ test.id }}">{{ test.right }}</span></div>{% endif %}
                        {% if test.top_heading %}<div><strong>Top Heading:</strong> <span class="editable" data-field="top_heading" data-id="{{ test.id }}">{{ test.top_heading }}</span></div>{% endif %}
                        {% if test.left_heading %}<div><strong>Left Heading:</strong> <span class="editable" data-field="left_heading" data-id="{{ test.id }}">{{ test.left_heading }}</span></div>{% endif %}
                    {% elif test.type == 'math' %}
                        <div><strong>Math:</strong> <span class="editable" data-field="math" data-id="{{ test.id }}">{{ test.math }}</span></div>
                    {% endif %}
                    <div><strong>Max Diffs:</strong> {{ test.max_diffs }}</div>
                    <div><strong>Status:</strong> {{ test.checked or 'Not checked' }}</div>
                </div>
            </div>
            {% endfor %}
        </div>
    </div>
    
    <script>
        // Set up PDF.js worker
        pdfjsLib.GlobalWorkerOptions.workerSrc = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js';
        
        // PDF rendering variables
        let pdfDoc = null;
        let pageNum = 1;
        let pageRendering = false;
        let pageNumPending = null;
        let scale = 1.5;
        const canvas = document.getElementById('pdf-canvas');
        const container = document.getElementById('pdf-container');
        const textLayer = document.getElementById('text-layer');
        const ctx = canvas.getContext('2d');
        const loadingIndicator = document.getElementById('loading-indicator');
        
        // Device pixel ratio for HiDPI displays
        const pixelRatio = window.devicePixelRatio || 1;
        
        // Load PDF from the provided path
        const pdfPath = '{{ pdf_path }}';
        
        // Function to create text layers with proper alignment
        function createTextLayer(textContent, viewport) {
            // Clear previous text layer
            textLayer.innerHTML = '';
            
            // Set text layer dimensions to match viewport
            textLayer.style.width = `${viewport.width}px`;
            textLayer.style.height = `${viewport.height}px`;

            // Process each text item
            const items = [];
            for (let i = 0; i < textContent.items.length; i++) {
                const item = textContent.items[i];
                
                // Convert text coordinates to viewport ones
                const tx = pdfjsLib.Util.transform(
                    viewport.transform,
                    item.transform
                );
                
                // Calculate text dimensions
                const fontHeight = Math.sqrt((tx[2] * tx[2]) + (tx[3] * tx[3]));
                const angle = Math.atan2(tx[1], tx[0]);
                
                // Create text span
                const span = document.createElement('span');
                span.textContent = item.str;
                span.dataset.text = item.str; // Store for searching
                
                // Set font styles
                span.style.fontSize = `${fontHeight}px`;
                
                // Adjust for baseline - the critical part for alignment
                span.style.left = `${tx[4]}px`;
                span.style.top = `${tx[5]}px`;
                
                // Handle text rotation
                if (angle !== 0) {
                    span.style.transform = `rotate(${angle}rad)`;
                    span.style.transformOrigin = '0% 0%';
                }
                
                textLayer.appendChild(span);
                items.push(span);
            }
            
            return items;
        }
        
        // Function to render a page
        function renderPage(num) {
            pageRendering = true;
            loadingIndicator.style.display = 'block';
            
            // Get page from PDF document
            pdfDoc.getPage(num).then(function(page) {
                // Create viewport at the requested scale
                const viewport = page.getViewport({ scale: scale });
                
                // Handle high-DPI displays
                canvas.width = viewport.width * pixelRatio;
                canvas.height = viewport.height * pixelRatio;
                canvas.style.width = `${viewport.width}px`;
                canvas.style.height = `${viewport.height}px`;
                
                // Set up canvas for rendering
                ctx.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
                
                // Render PDF page
                const renderContext = {
                    canvasContext: ctx,
                    viewport: viewport,
                };
                
                const renderTask = page.render(renderContext);
                
                // Process text content for text layer
                page.getTextContent().then(function(textContent) {
                    createTextLayer(textContent, viewport);
                });
                
                // Wait for rendering to finish
                renderTask.promise.then(function() {
                    pageRendering = false;
                    loadingIndicator.style.display = 'none';
                    
                    if (pageNumPending !== null) {
                        // New page rendering is pending
                        renderPage(pageNumPending);
                        pageNumPending = null;
                    }
                }).catch(function(error) {
                    console.error('Error rendering PDF page:', error);
                    loadingIndicator.style.display = 'none';
                    pageRendering = false;
                });
            }).catch(function(error) {
                console.error('Error getting PDF page:', error);
                loadingIndicator.style.display = 'none';
                pageRendering = false;
            });
            
            // Update page counters
            document.getElementById('page-num').textContent = num;
        }
        
        // Function to queue rendering if already in progress
        function queueRenderPage(num) {
            if (pageRendering) {
                pageNumPending = num;
            } else {
                renderPage(num);
            }
        }
        
        // Handle page navigation
        function onPrevPage() {
            if (pageNum <= 1) {
                return;
            }
            pageNum--;
            queueRenderPage(pageNum);
        }
        
        function onNextPage() {
            if (pageNum >= pdfDoc.numPages) {
                return;
            }
            pageNum++;
            queueRenderPage(pageNum);
        }
        
        // Zoom functions
        function zoomIn() {
            scale *= 1.25;
            queueRenderPage(pageNum);
        }
        
        function zoomOut() {
            scale /= 1.25;
            queueRenderPage(pageNum);
        }
        
        // Register event listeners
        document.getElementById('prev-page').addEventListener('click', onPrevPage);
        document.getElementById('next-page').addEventListener('click', onNextPage);
        document.getElementById('zoom-in').addEventListener('click', zoomIn);
        document.getElementById('zoom-out').addEventListener('click', zoomOut);
        
        // Load PDF
        loadingIndicator.style.display = 'block';
        pdfjsLib.getDocument(pdfPath).promise.then(function(pdf) {
            pdfDoc = pdf;
            document.getElementById('page-count').textContent = pdf.numPages;
            
            // Initial page render
            renderPage(pageNum);
        }).catch(function(error) {
            console.error('Error loading PDF:', error);
            loadingIndicator.style.display = 'none';
        });
        
        // Highlight text in PDF
        function highlightText(testId) {
            // Clear any existing highlights
            clearHighlights();
            
            let searchText = '';
            let test = document.querySelector(`.test-item[data-id="${testId}"]`);
            
            // Extract the text to search based on the test type
            if (test) {
                const testType = test.querySelector('.test-type').textContent.toLowerCase();
                
                if (testType === 'present' || testType === 'absent') {
                    const textElement = test.querySelector(`[data-field="text"][data-id="${testId}"]`);
                    if (textElement) {
                        searchText = textElement.textContent;
                    }
                } else if (testType === 'order') {
                    const beforeElement = test.querySelector(`[data-field="before"][data-id="${testId}"]`);
                    const afterElement = test.querySelector(`[data-field="after"][data-id="${testId}"]`);
                    if (beforeElement && afterElement) {
                        searchText = beforeElement.textContent + ' ' + afterElement.textContent;
                    }
                } else if (testType === 'table') {
                    const cellElement = test.querySelector(`[data-field="cell"][data-id="${testId}"]`);
                    if (cellElement) {
                        searchText = cellElement.textContent;
                    }
                    
                    // Also get related cell contents
                    const fields = ['up', 'down', 'left', 'right', 'top_heading', 'left_heading'];
                    fields.forEach(field => {
                        const element = test.querySelector(`[data-field="${field}"][data-id="${testId}"]`);
                        if (element) {
                            searchText += ' ' + element.textContent;
                        }
                    });
                } else if (testType === 'math') {
                    const mathElement = test.querySelector(`[data-field="math"][data-id="${testId}"]`);
                    if (mathElement) {
                        searchText = mathElement.textContent;
                    }
                }
            }
            
            if (searchText) {
                // Search and highlight the text in the PDF
                const textElements = document.querySelectorAll('#text-layer span');
                const searchTerms = searchText.split(/\s+/).filter(term => term.length > 2);
                
                textElements.forEach(element => {
                    const elementText = element.dataset.text;
                    
                    searchTerms.forEach(term => {
                        if (elementText.includes(term)) {
                            element.classList.add('highlight');
                        }
                    });
                });
                
                // Scroll to the first highlight
                const firstHighlight = document.querySelector('#text-layer .highlight');
                if (firstHighlight) {
                    firstHighlight.scrollIntoView({ behavior: 'smooth', block: 'center' });
                }
            }
        }
        
        function clearHighlights() {
            const highlightedElements = document.querySelectorAll('#text-layer .highlight');
            highlightedElements.forEach(element => {
                element.classList.remove('highlight');
            });
        }
        
        // Function to update test status (approve/reject)
        function updateTestStatus(pdfName, testId, field, value) {
            fetch('/update_test', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    pdf: pdfName,
                    id: testId,
                    field: field,
                    value: value
                }),
            })
            .then(response => response.json())
            .then(data => {
                // Update UI to reflect change
                const testItem = document.querySelector(`.test-item[data-id="${testId}"]`);
                testItem.classList.remove('status-approved', 'status-rejected');
                
                if (value === 'verified') {
                    testItem.classList.add('status-approved');
                } else if (value === 'rejected') {
                    testItem.classList.add('status-rejected');
                }
            })
            .catch(error => {
                console.error('Error updating test:', error);
            });
        }
        
        // Toggle edit mode for a field
        function toggleEditMode(testId) {
            const editables = document.querySelectorAll(`.editable[data-id="${testId}"]`);
            
            editables.forEach(editable => {
                const field = editable.dataset.field;
                const currentValue = editable.innerText;
                
                // Create textarea
                const textarea = document.createElement('textarea');
                textarea.value = currentValue;
                textarea.dataset.field = field;
                textarea.dataset.originalValue = currentValue;
                
                // Replace the span with textarea
                editable.parentNode.replaceChild(textarea, editable);
                
                // Focus the textarea
                textarea.focus();
                
                // Add blur event to save changes
                textarea.addEventListener('blur', function() {
                    const newValue = this.value;
                    const pdfName = '{{ pdf_name }}';
                    
                    // If value changed, save it
                    if (newValue !== this.dataset.originalValue) {
                        updateTestStatus(pdfName, testId, field, newValue);
                    }
                    
                    // Create span again
                    const span = document.createElement('span');
                    span.className = 'editable';
                    span.dataset.field = field;
                    span.dataset.id = testId;
                    span.innerText = newValue;
                    
                    // Replace textarea with span
                    this.parentNode.replaceChild(span, this);
                });
            });
        }
    </script>
</body>
</html>""")
    
    # Create all done template
    all_done_template = os.path.join(templates_dir, 'all_done.html')
    with open(all_done_template, 'w') as f:
        f.write("""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>All Tests Reviewed</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 0;
            padding: 20px;
            background-color: #f5f5f5;
            display: flex;
            justify-content: center;
            align-items: center;
            height: 100vh;
            text-align: center;
        }
        
        .message {
            background-color: white;
            padding: 40px;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0, 0, 0, 0.1);
        }
        
        h1 {
            color: #28a745;
        }
    </style>
</head>
<body>
    <div class="message">
        <h1>All Tests Reviewed!</h1>
        <p>You have completed reviewing all tests in the dataset.</p>
    </div>
</body>
</html>""")


def main():
    """Main entry point with command-line arguments."""
    global DATASET_DIR, PDF_TESTS, ALL_PDFS
    
    parser = argparse.ArgumentParser(description="Interactive Test Review App")
    parser.add_argument("dataset_dir", help="Path to the dataset directory containing table_tests.jsonl and pdfs/ folder")
    parser.add_argument("--port", type=int, default=5000, help="Port for the Flask app")
    parser.add_argument("--host", default="127.0.0.1", help="Host for the Flask app")
    parser.add_argument("--debug", action="store_true", help="Run Flask in debug mode")
    
    args = parser.parse_args()
    
    # Validate dataset directory
    if not os.path.isdir(args.dataset_dir):
        print(f"Error: Dataset directory not found: {args.dataset_dir}")
        return 1
    
    pdf_dir = os.path.join(args.dataset_dir, "pdfs")
    if not os.path.isdir(pdf_dir):
        print(f"Error: PDF directory not found: {pdf_dir}")
        return 1
    
    # Store dataset directory globally
    DATASET_DIR = args.dataset_dir
    
    # Load dataset
    try:
        PDF_TESTS, ALL_PDFS = load_dataset(args.dataset_dir)
    except Exception as e:
        print(f"Error loading dataset: {str(e)}")
        return 1
    
    # Create templates directory
    create_templates_directory()
    
    # Find first PDF with unchecked tests
    CURRENT_PDF = find_next_unchecked_pdf()
    
    # Start Flask app
    print(f"Starting server at http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=args.debug)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())