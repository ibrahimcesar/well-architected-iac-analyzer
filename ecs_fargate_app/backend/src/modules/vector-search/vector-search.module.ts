import { Module } from '@nestjs/common';
import { VectorSearchService } from './vector-search.service';
import { AwsConfigService } from '../../config/aws.config';
import { ConfigModule } from '@nestjs/config';

@Module({
    imports: [ConfigModule],
    providers: [VectorSearchService, AwsConfigService],
    exports: [VectorSearchService],
})
export class VectorSearchModule {}
