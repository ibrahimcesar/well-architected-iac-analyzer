/**
 * Builds a prompt for generating IaC templates from architecture diagrams
 * @param previousSections Number of sections already generated
 * @param allPreviousSections Content of all previously generated sections
 * @param recommendations Recommendations to consider when generating IaC
 * @param supportingDocName Name of supporting document (if any)
 * @param supportingDocDescription Description of supporting document (if any)
 * @returns A prompt for IaC template generation
 */
export function buildIacGenerationPrompt(
    previousSections: number,
    allPreviousSections: string,
    recommendations: any[],
    supportingDocName?: string,
    supportingDocDescription?: string
): string {
    let prompt = `
    <task>Based on the architecture diagram and the recommendations within the <recommendations> section below, generate an IaC template.</task>
    
    <context>
    <previous_sections_count>${previousSections}</previous_sections_count>
    <previous_responses>
    ${allPreviousSections}
    </previous_responses>
    
    <recommendations>
    ${JSON.stringify(recommendations, null, 2)}
    </recommendations>
    </context>`;

    // Add supporting document reference if provided
    if (supportingDocName && supportingDocDescription) {
      prompt += `
      
    <supporting_document>
    <description>
    The attached file named "${supportingDocName}" has been provided as a supporting document that contains CRITICAL context to consider when generating the IaC template. This document may be in any format (.pdf, .txt, .png, .jpg, .jpeg) and requires your careful analysis.

    IMPORTANT INSTRUCTIONS:
    1. Begin by thoroughly examining this supporting document BEFORE analyzing the architecture diagram image and generating the IaC template
    2. Extract all relevant information, especially:
      - Account-level or organization-level services/configurations
      - Pre-existing infrastructure that interacts with the architecture diagram image
      - Security controls, governance mechanisms, and operational procedures
      - Architectural patterns and relationships not visible in the architecture diagram image alone
    
    3. When assessing best practices, consider the COMPLETE ARCHITECTURE:
      - The architecture diagram image may implement workload-specific resources
      - The supporting document may show broader context (account-level services, shared resources)
    
    4. If your IaC template generation is influenced by this supporting document:
      - Begin your reasoning with "[From Supporting Doc]" 
      - Explicitly state which elements from the supporting document led to your conclusion
      - Explain how these elements relate to the resources defined in the architecture diagram image
    
    Below is a brief description of the supporting document attached: 
    ${supportingDocDescription}
    </description>
    </supporting_document>
    `;
    }

    return prompt;
}