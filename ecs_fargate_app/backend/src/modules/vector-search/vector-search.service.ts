import { Injectable, Logger } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { AwsConfigService } from '../../config/aws.config';
import { GetObjectCommand, ListObjectsV2Command } from '@aws-sdk/client-s3';
import { InvokeModelCommand } from '@aws-sdk/client-bedrock-runtime';

interface VectorChunk {
    id: string;
    text: string;
    embedding: number[];
    metadata: {
        lens_name: string;
        pillar: string;
        source_file: string;
        chunk_index: number;
        start_char: number;
        end_char: number;
        processed_at: string;
    };
}

interface SearchResult {
    text: string;
    score: number;
    metadata: any;
}

@Injectable()
export class VectorSearchService {
    private readonly logger = new Logger(VectorSearchService.name);
    private readonly vectorsBucket: string;
    private readonly embeddingModel: string;
    private readonly embeddingDimensions: number;

    constructor(
        private readonly awsConfig: AwsConfigService,
        private readonly configService: ConfigService,
    ) {
        this.vectorsBucket = this.configService.get<string>('aws.s3.vectorsBucket');
        this.embeddingModel = this.configService.get<string>('aws.bedrock.embeddingModel', 'amazon.titan-embed-text-v2:0');
        this.embeddingDimensions = this.configService.get<number>('aws.bedrock.embeddingDimensions', 1024);
    }

    /**
     * Generate embedding vector for a text query
     */
    async generateEmbedding(text: string): Promise<number[]> {
        try {
            const bedrockClient = this.awsConfig.createBedrockClient();

            const command = new InvokeModelCommand({
                modelId: this.embeddingModel,
                body: JSON.stringify({
                    inputText: text,
                    dimensions: this.embeddingDimensions,
                    normalize: true
                })
            });

            const response = await bedrockClient.send(command);
            const responseBody = JSON.parse(new TextDecoder().decode(response.body));

            return responseBody.embedding;
        } catch (error) {
            this.logger.error('Error generating embedding:', error);
            throw new Error(`Failed to generate embedding: ${error.message}`);
        }
    }

    /**
     * Calculate cosine similarity between two vectors
     */
    private cosineSimilarity(vecA: number[], vecB: number[]): number {
        if (vecA.length !== vecB.length) {
            throw new Error('Vectors must have the same length');
        }

        let dotProduct = 0;
        let normA = 0;
        let normB = 0;

        for (let i = 0; i < vecA.length; i++) {
            dotProduct += vecA[i] * vecB[i];
            normA += vecA[i] * vecA[i];
            normB += vecB[i] * vecB[i];
        }

        return dotProduct / (Math.sqrt(normA) * Math.sqrt(normB));
    }

    /**
     * Load vector chunks from S3 for a specific lens and pillar
     */
    private async loadVectorChunks(
        lensName: string,
        pillar?: string
    ): Promise<VectorChunk[]> {
        try {
            const s3Client = this.awsConfig.createS3Client();
            const lensKey = lensName.toLowerCase().replace(/\s+/g, '-');
            const pillarKey = pillar ? pillar.toLowerCase().replace(/\s+/g, '-') : '';

            // Construct prefix based on whether pillar is specified
            const prefix = pillar
                ? `embeddings/${lensKey}/${pillarKey}/`
                : `embeddings/${lensKey}/`;

            // List all vector files
            const listCommand = new ListObjectsV2Command({
                Bucket: this.vectorsBucket,
                Prefix: prefix
            });

            const listResponse = await s3Client.send(listCommand);

            if (!listResponse.Contents || listResponse.Contents.length === 0) {
                this.logger.warn(`No vector chunks found for lens: ${lensName}, pillar: ${pillar || 'all'}`);
                return [];
            }

            // Load all chunks
            const chunks: VectorChunk[] = [];

            for (const object of listResponse.Contents) {
                if (!object.Key) continue;

                const getCommand = new GetObjectCommand({
                    Bucket: this.vectorsBucket,
                    Key: object.Key
                });

                const response = await s3Client.send(getCommand);
                const chunkData = await response.Body?.transformToString();

                if (chunkData) {
                    chunks.push(JSON.parse(chunkData));
                }
            }

            return chunks;
        } catch (error) {
            this.logger.error('Error loading vector chunks:', error);
            throw new Error(`Failed to load vector chunks: ${error.message}`);
        }
    }

    /**
     * Search for similar vectors using cosine similarity
     */
    async searchSimilarVectors(
        query: string,
        lensName: string,
        pillar?: string,
        topK: number = 10
    ): Promise<SearchResult[]> {
        try {
            // Generate query embedding
            const queryEmbedding = await this.generateEmbedding(query);

            // Load relevant vector chunks
            const chunks = await this.loadVectorChunks(lensName, pillar);

            if (chunks.length === 0) {
                this.logger.warn('No chunks available for search');
                return [];
            }

            // Calculate similarity scores
            const scoredChunks = chunks.map(chunk => ({
                text: chunk.text,
                score: this.cosineSimilarity(queryEmbedding, chunk.embedding),
                metadata: chunk.metadata
            }));

            // Sort by score (descending) and take top-k
            const topResults = scoredChunks
                .sort((a, b) => b.score - a.score)
                .slice(0, topK);

            return topResults;
        } catch (error) {
            this.logger.error('Error searching vectors:', error);
            throw new Error(`Failed to search vectors: ${error.message}`);
        }
    }

    /**
     * Retrieve context for a Well-Architected question
     */
    async retrieveContext(
        pillar: string,
        question: string,
        lensName: string = 'Well-Architected Framework',
        topK: number = 10
    ): Promise<string[]> {
        try {
            // Construct search query
            const searchQuery = `${pillar}: ${question}`;

            // Search for similar vectors
            const results = await this.searchSimilarVectors(
                searchQuery,
                lensName,
                pillar,
                topK
            );

            // Extract text from results
            return results.map(result => result.text);
        } catch (error) {
            this.logger.error('Error retrieving context:', error);
            throw new Error(`Failed to retrieve context: ${error.message}`);
        }
    }

    /**
     * Check if vectors exist for a specific lens and pillar
     */
    async vectorsExist(lensName: string, pillar?: string): Promise<boolean> {
        try {
            const s3Client = this.awsConfig.createS3Client();
            const lensKey = lensName.toLowerCase().replace(/\s+/g, '-');
            const pillarKey = pillar ? pillar.toLowerCase().replace(/\s+/g, '-') : '';

            const prefix = pillar
                ? `embeddings/${lensKey}/${pillarKey}/`
                : `embeddings/${lensKey}/`;

            const listCommand = new ListObjectsV2Command({
                Bucket: this.vectorsBucket,
                Prefix: prefix,
                MaxKeys: 1
            });

            const response = await s3Client.send(listCommand);
            return (response.Contents?.length || 0) > 0;
        } catch (error) {
            this.logger.error('Error checking vector existence:', error);
            return false;
        }
    }
}
