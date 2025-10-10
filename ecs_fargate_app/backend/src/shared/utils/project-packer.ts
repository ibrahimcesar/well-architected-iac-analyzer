import * as fs from 'fs';
import * as path from 'path';
import * as os from 'os';
import * as AdmZip from 'adm-zip';

import { ProjectFile, PackedProject } from '../interfaces/project-file.interface';
import { Logger } from '@nestjs/common';

/**
 * Class that provides functionality to pack a project from a zip file or multiple files
 */
export class ProjectPacker {
    private readonly logger = new Logger(ProjectPacker.name);

    // Media and binary file extensions to exclude
    private readonly excludeExtensions = [
        '.env', '.log', '.tmp', '.pdf', '.png', '.jpeg', '.jpg', '.gif',
        '.bmp', '.tiff', '.raw', '.cr2', '.nef', '.arw', '.dng', '.psd',
        '.ai', '.eps', '.mov', '.mp4', '.avi', '.mkv', '.wmv', '.flv',
        '.webm', '.m4v', '.3gp', '.mpeg', '.mpg', '.mp3', '.wav', '.aac',
        '.wma', '.ogg', '.flac', '.m4a', '.mid', '.midi', '.doc', '.docx',
        '.ppt', '.pptx', '.xls', '.xlsx', '.zip', '.rar', '.7z', '.tar.gz',
        '.gz', '.iso', '.dmg', '.exe', '.dll', '.app', '.deb', '.rpm', '.msi',
        '.bin', '.dat', '.db', '.sqlite', '.mdb', '.pdb', '.obj', '.lib', '.so',
        '.dylib', '.class', '.jar', '.pyc', '.ico', '.cur', '.heic', '.heif',
        '.webp', '.svg', '.xcf', '.sketch', '.fig', '.dwg', '.dxf', '.blend',
        '.fbx', '.3ds', '.max', '.mb', '.ma', '.swf'
    ];

    // Patterns to exclude (directories and files)
    private readonly excludePatterns = [
        'node_modules', 'dist', 'cdk.out', '.git', '.github',
        'package-lock.json', 'yarn.lock'
    ];

    // Token limit warning threshold
    private readonly tokenLimitWarning = 200000;

    /**
     * Unzips a buffer containing a zip file
     * @param buffer The zip file as a Buffer
     * @returns Directory path where files were extracted
     */
    private async unzipBuffer(buffer: Buffer): Promise<string> {
        const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'project-'));
        const zip = new AdmZip(buffer);

        // Extract all files
        zip.extractAllTo(tempDir, true);

        return tempDir;
    }

    /**
     * Generates a directory tree structure for a given path
     * @param dirPath Directory path
     * @returns String representation of the directory tree
     */
    private generateDirectoryTree(dirPath: string): string {
        try {
            const files = this.getFilesRecursively(dirPath);
            return this.formatAsTree(files, dirPath);
        } catch (error) {
            this.logger.error(`Error generating directory tree: ${error}`);
            return `${path.basename(dirPath)} (directory tree generation failed)`;
        }
    }

    /**
     * Recursively gets all files from a directory, excluding patterns
     * @param dirPath Directory path
     * @returns Array of file paths relative to dirPath
     */
    private getFilesRecursively(dirPath: string): string[] {
        const files: string[] = [];

        const traverse = (currentPath: string) => {
            const entries = fs.readdirSync(currentPath);

            for (const entry of entries) {
                const fullPath = path.join(currentPath, entry);
                const relativePath = path.relative(dirPath, fullPath);

                // Skip excluded patterns
                if (this.excludePatterns.some(pattern => relativePath.includes(pattern))) {
                    continue;
                }

                // Skip hidden files/directories
                if (entry.startsWith('.')) {
                    continue;
                }

                const stats = fs.statSync(fullPath);
                if (stats.isDirectory()) {
                    traverse(fullPath);
                } else {
                    files.push(relativePath);
                }
            }
        };

        traverse(dirPath);
        return files.sort();
    }

    /**
     * Formats file paths as a tree structure
     * @param files Array of file paths
     * @param basePath Base directory path
     * @returns Formatted tree string
     */
    private formatAsTree(files: string[], basePath: string): string {
        const baseDir = path.basename(basePath);
        let result = `${baseDir}\n.`;

        files.forEach(file => {
            const segments = file.split(path.sep).filter(s => s);
            let currentPath = '';
            
            segments.forEach((segment, index) => {
                const depth = index;
                const isLast = index === segments.length - 1;
                const prefix = '│   '.repeat(depth) + (isLast ? '└── ' : '├── ');
                
                if (index === 0 || !result.includes(currentPath + segment)) {
                    result += '\n' + prefix + segment;
                }
                currentPath += segment + '/';
            });
        });

        return result;
    }

    /**
     * Reads all files from a directory recursively
     * @param dirPath Directory path
     * @returns Array of ProjectFile objects
     */
    private async readFilesFromDirectory(dirPath: string): Promise<ProjectFile[]> {
        const files: ProjectFile[] = [];

        const processDirectory = async (currentPath: string, basePath: string) => {
            const entries = fs.readdirSync(currentPath);

            for (const entry of entries) {
                const fullPath = path.join(currentPath, entry);
                const relativePath = path.relative(basePath, fullPath);

                // Skip excluded directories
                if (this.excludePatterns.some(pattern => fullPath.includes(pattern))) {
                    continue;
                }

                const stats = fs.statSync(fullPath);

                if (stats.isDirectory()) {
                    await processDirectory(fullPath, basePath);
                } else {
                    // Skip excluded file extensions
                    const ext = path.extname(fullPath).toLowerCase();
                    if (this.excludeExtensions.includes(ext)) {
                        continue;
                    }

                    try {
                        const content = fs.readFileSync(fullPath, 'utf8');
                        files.push({
                            path: relativePath,
                            filename: path.basename(fullPath),
                            content,
                            size: stats.size
                        });
                    } catch (error) {
                        // Skip files that can't be read as text
                        this.logger.warn(`Skipping file ${fullPath}: ${error.message}`);
                    }
                }
            }
        };

        await processDirectory(dirPath, dirPath);
        return files;
    }

    /**
     * Creates a packed representation of a project
     * @param files Array of ProjectFile objects
     * @param directoryStructure Directory tree string
     * @param source Source information
     * @returns PackedProject object
     */
    private createPackedProject(
        files: ProjectFile[],
        directoryStructure: string,
        source: string
    ): PackedProject {
        // Create the packed content
        let packedContent = `================================================================
Project Packed File Summary
================================================================

Purpose:
--------
This Project Packed file contains a packed representation of an entire project or repository's contents.

File Format:
------------
The content is organized as follows:
1. This summary section
2. Directory structure
3. Multiple file entries, each consisting of:
  a. A separator line (================)
  b. The file path (File: path/to/file)
  c. Another separator line (================)
  d. The full contents of the file

Notes:
------
- Some files have been excluded based on exclusion rules when generating this packed file.
- Binary files are not included in this packed representation.
- Source: ${source}

================================================================
Directory Structure
================================================================
${directoryStructure}

================================================================
Files
================================================================

Below is the content of each file in the project:

`;

        // Add each file to the packed content
        files.forEach(file => {
            packedContent += `\n================\n`;
            packedContent += `File: ${file.path}\n`;
            packedContent += `================\n`;
            packedContent += `${file.content}\n`;
        });

        // Calculate token count (rough estimate: 4 chars per token)
        const tokenCount = Math.ceil(packedContent.length / 4);
        const exceedsTokenLimit = tokenCount > this.tokenLimitWarning;

        return {
            source,
            directoryStructure,
            files,
            tokenCount,
            exceedsTokenLimit,
            packedContent
        };
    }

    /**
     * Process a zip file and create a packed project
     * @param buffer Zip file buffer
     * @param originalFilename Original zip filename
     * @returns PackedProject object
     */
    public async processZipFile(buffer: Buffer, originalFilename: string): Promise<PackedProject> {
        try {
            // Unzip the file to a temporary directory
            const tempDir = await this.unzipBuffer(buffer);

            // Generate directory tree
            const directoryStructure = this.generateDirectoryTree(tempDir);

            // Read all files
            const files = await this.readFilesFromDirectory(tempDir);

            // Create packed project
            const packedProject = this.createPackedProject(files, directoryStructure, `Zip file: ${originalFilename}`);

            // Clean up temp directory
            fs.rm(tempDir, { recursive: true, force: true }, (err) => {
                if (err) {
                    this.logger.error(`Error deleting temp directory: ${err}`);
                }
            });

            return packedProject;
        } catch (error) {
            this.logger.error(`Error processing zip file: ${error}`);
            throw new Error(`Failed to process zip file: ${error.message}`);
        }
    }

    /**
     * Process multiple files and create a packed project
     * @param files Array of {filename, buffer} objects
     * @returns PackedProject object
     */
    public async processMultipleFiles(
        files: Array<{ filename: string, buffer: Buffer, type: string }>
    ): Promise<PackedProject> {
        try {
            // Create temporary directory
            const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'multi-files-'));

            // Write files to temp directory
            files.forEach(file => {
                try {
                    const filePath = path.join(tempDir, file.filename);
                    fs.writeFileSync(filePath, file.buffer);
                } catch (error) {
                    this.logger.error(`Error writing file ${file.filename}: ${error}`);
                }
            });

            // Generate directory tree
            const directoryStructure = this.generateDirectoryTree(tempDir);

            // Read all files
            const projectFiles = await this.readFilesFromDirectory(tempDir);

            // Create packed project
            const packedProject = this.createPackedProject(
                projectFiles,
                directoryStructure,
                `Multiple files: ${files.map(f => f.filename).join(', ')}`
            );

            // Clean up temp directory
            fs.rm(tempDir, { recursive: true, force: true }, (err) => {
                if (err) {
                    this.logger.error(`Error deleting temp directory: ${err}`);
                }
            });

            return packedProject;
        } catch (error) {
            this.logger.error(`Error processing multiple files: ${error}`);
            throw new Error(`Failed to process multiple files: ${error.message}`);
        }
    }

    /**
     * Creates a zip file from multiple files
     * @param files Array of {filename, buffer} objects
     * @returns Buffer containing the zip file
     */
    public createZipFromFiles(
        files: Array<{ filename: string, buffer: Buffer, type: string }>
    ): Buffer {
        try {
            const zip = new AdmZip();

            // Add each file to the zip
            files.forEach(file => {
                zip.addFile(file.filename, file.buffer);
            });

            // Generate zip buffer
            return zip.toBuffer();
        } catch (error) {
            this.logger.error(`Error creating zip file: ${error}`);
            throw new Error(`Failed to create zip file: ${error.message}`);
        }
    }
}