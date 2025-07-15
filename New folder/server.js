
const express = require('express');
const multer = require('multer');
const ExcelJS = require('exceljs');
const { OpenAI } = require('openai');
const cors = require('cors');
const stream = require('stream');

// Initialize Express app
const app = express();
const port = 3000;

// Use CORS to allow frontend requests
app.use(cors());

// --- OpenAI API Configuration ---
// WARNING: It is not secure to hardcode API keys in source code.
// It is recommended to use environment variables.
const client = new OpenAI({
    baseURL: "https://openrouter.ai/api/v1",
    apiKey: "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",
});

// --- Multer Configuration for File Uploads ---
// We use memoryStorage to process the file without saving it to disk.
const storage = multer.memoryStorage();
const upload = multer({ storage: storage });

// --- Helper function to introduce a delay ---
const delay = (ms) => new Promise(resolve => setTimeout(resolve, ms));

// --- AI Commodity Check Function ---
async function checkCommodity(commodity) {
    if (!commodity) {
        return 'N'; // Return 'N' for empty values
    }
    const prompt = `Does the phrase '${commodity}' mean 'FLUID SEAMLESS STEEL' or something similar in meaning? Return only 'Y' if it matches or is similar, 'N' if it does not.`;

    try {
        const completion = await client.chat.completions.create({
            model: "openai/gpt-4o-mini",
            messages: [{ role: "user", content: prompt }],
            extra_headers: {
                "HTTP-Referer": "http://localhost", // Example Referer
                "X-Title": "Excel Processor"      // Example Title
            },
        });
        const result = completion.choices[0].message.content.trim();
        return (result === 'Y' || result === 'N') ? result : 'N';
    } catch (error) {
        console.error(`Error processing '${commodity}':`, error);
        return 'N'; // Return 'N' on error
    }
}

// --- API Endpoint for Processing Excel Files ---
app.post('/process-excel', upload.single('file'), async (req, res) => {
    if (!req.file) {
        return res.status(400).send('No file uploaded.');
    }

    try {
        // Read the uploaded file buffer into an ExcelJS workbook
        const workbook = new ExcelJS.Workbook();
        const buffer = req.file.buffer;
        await workbook.xlsx.load(buffer);

        const worksheet = workbook.worksheets[0];
        if (!worksheet) {
            return res.status(400).send('No worksheet found in the Excel file.');
        }

        // Find the header row to locate columns by name
        const headerRow = worksheet.getRow(1).values;
        const serialIndex = headerRow.indexOf('Serial');
        const commodityIndex = headerRow.indexOf('COMMODITY');

        if (serialIndex === -1 || commodityIndex === -1) {
            return res.status(400).send('Required columns "Serial" or "COMMODITY" not found.');
        }

        // Add the new header for the results column
        worksheet.getCell(1, headerRow.length + 1).value = 'Matches_FLUID_SEAMLESS_STEEL';

        // Process each row with a delay
        for (let i = 2; i <= worksheet.rowCount; i++) {
            const row = worksheet.getRow(i);
            const commodity = row.getCell(commodityIndex).value;
            
            const result = await checkCommodity(commodity);
            row.getCell(headerRow.length + 1).value = result;
            
            await delay(500); // 0.5-second delay between API calls
        }
        
        // Set headers for file download
        res.setHeader(
            'Content-Type',
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        );
        res.setHeader(
            'Content-Disposition',
            'attachment; filename=Result_with_Confirm.xlsx'
        );

        // Write the processed workbook to the response
        const processedBuffer = await workbook.xlsx.writeBuffer();
        const readable = new stream.PassThrough();
        readable.end(processedBuffer);
        readable.pipe(res);

    } catch (error) {
        console.error('Failed to process file:', error);
        res.status(500).send('An error occurred while processing the file.');
    }
});

// --- Root Endpoint ---
app.get('/', (req, res) => {
    res.send('Welcome to the Excel Processing API. Use the /process-excel endpoint to upload a file.');
});

// --- Start the Server ---
app.listen(port, () => {
    console.log(`Server is running on http://localhost:${port}`);
});
