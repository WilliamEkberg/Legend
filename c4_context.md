# C4 Model Research

## Overview

The C4 model is an "abstraction-first" approach to software architecture diagramming created by Simon Brown (2006-2011). It provides a hierarchical way to visualize software architecture at different levels of detail, similar to how Google Maps allows zooming.

## The Four Levels

### Level 1: System Context Diagram
- **Purpose**: Shows how your software system fits into the world
- **Elements**: People, the software system, external systems
- **Audience**: Technical and non-technical stakeholders
- **What to extract**: Entry points, external integrations, user types

### Level 2: Container Diagram (modules)
- **Purpose**: High-level technical building blocks
- **What a Container is**: A separately deployable/runnable unit
  - Server-side web applications
  - Single-page applications
  - Desktop/mobile apps
  - Database schemas
  - File systems
  - Message queues
  - Microservices
- **Audience**: Architects, developers, operations
- **What to extract**: Build artifacts, deployment units, data stores, communication patterns

### Level 3: Component Diagram
- **Purpose**: Internal structure of a container
- **What a Component is**: Grouping of related functionality behind a well-defined interface
  - OOP: Classes/interfaces implementing a feature
  - Procedural: Related C files
  - Functional: Modules with related functions
- **Audience**: Architects, developers
- **What to extract**: Package/namespace boundaries, interface definitions, feature groupings

### Level 4: Code Diagram
- **Purpose**: Implementation details (classes, interfaces)
- **Representation**: UML class diagrams, entity-relationship diagrams
- **Recommendation**: Rarely needed, should be auto-generated if used
- **What to extract**: Class relationships, inheritance, method signatures
