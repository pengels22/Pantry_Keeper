import Foundation

struct DashboardResponse: Codable {
    var products: [PantryProduct]
    var receiptCount: Int
    var unknownCount: Int

    enum CodingKeys: String, CodingKey {
        case products
        case receiptCount = "receipt_count"
        case unknownCount = "unknown_count"
    }
}

struct PantryProduct: Codable, Identifiable, Hashable {
    var id: Int
    var upc: String?
    var notes: String?
    var receiptCodeRaw: String?
    var gtinNormalized: String?
    var brand: String?
    var name: String
    var size: String?
    var unit: String?
    var category: String?
    var defaultLocation: String?
    var lookupSource: String?
    var inventoryQuantity: Double?
    var inventoryLocation: String?

    enum CodingKeys: String, CodingKey {
        case id, upc, notes, brand, name, size, unit, category
        case receiptCodeRaw = "receipt_code_raw"
        case gtinNormalized = "gtin_normalized"
        case defaultLocation = "default_location"
        case lookupSource = "lookup_source"
        case inventoryQuantity = "inventory_quantity"
        case inventoryLocation = "inventory_location"
    }
}

struct UnknownProduct: Codable, Identifiable, Hashable {
    var receiptItemID: Int
    var rawCode: String?
    var normalizedCode: String?
    var upc: String?
    var classification: String?
    var suggestion: ProductSuggestion?
    var description: String?
    var quantity: Double?
    var lineTotal: Double?
    var meijerSearchURL: String?

    var id: Int { receiptItemID }

    enum CodingKeys: String, CodingKey {
        case upc, classification, suggestion, description, quantity
        case receiptItemID = "receipt_item_id"
        case rawCode = "raw_code"
        case normalizedCode = "normalized_code"
        case lineTotal = "line_total"
        case meijerSearchURL = "meijer_search_url"
    }
}

struct ProductSuggestion: Codable, Hashable {
    var name: String?
    var brand: String?
    var size: String?
    var category: String?
    var unit: String?
    var notes: String?
    var defaultLocation: String?
    var lookupSource: String?
    var url: String?
    var matchKind: String?

    enum CodingKeys: String, CodingKey {
        case name, brand, size, category, unit, notes, url
        case defaultLocation = "default_location"
        case lookupSource = "lookup_source"
        case matchKind = "match_kind"
    }
}

struct ProductLookupResult: Codable {
    var upc: String?
    var rawCode: String?
    var normalizedCode: String?
    var status: String?
    var lookupStatus: String?
    var product: PantryProduct?
    var suggestion: ProductSuggestion?
    var candidates: [ProductSuggestion]?
    var meijerSearchURL: String?

    enum CodingKeys: String, CodingKey {
        case upc, status, product, suggestion, candidates
        case rawCode = "raw_code"
        case normalizedCode = "normalized_code"
        case lookupStatus = "lookup_status"
        case meijerSearchURL = "meijer_search_url"
    }
}

struct ReceiptScan: Codable {
    var sourceType: String
    var sourceFormat: String
    var rawText: String?
    var ocrText: String?
    var parsed: ParsedReceipt
    var resolution: ReceiptResolution?

    enum CodingKeys: String, CodingKey {
        case parsed, resolution
        case sourceType = "source_type"
        case sourceFormat = "source_format"
        case rawText = "raw_text"
        case ocrText = "ocr_text"
    }
}

struct ParsedReceipt: Codable, Hashable {
    var purchaseDate: String?
    var storeNumber: String?
    var terminal: String?
    var transactionNumber: String?
    var purchaseTime: String?
    var total: Double?
    var fingerprint: String?
    var expectedItemCount: Int?
    var items: [ReceiptLine]

    enum CodingKeys: String, CodingKey {
        case terminal, total, fingerprint, items
        case purchaseDate = "purchase_date"
        case storeNumber = "store_number"
        case transactionNumber = "transaction_number"
        case purchaseTime = "purchase_time"
        case expectedItemCount = "expected_item_count"
    }
}

struct ReceiptLine: Codable, Hashable {
    var rawCode: String?
    var upc: String?
    var normalizedCode: String?
    var receiptDescription: String?
    var quantity: Double?
    var lineTotal: Double?
    var unitPrice: Double?
    var status: String?
    var source: String?
    var product: PantryProduct?
    var suggestion: ProductSuggestion?
    var candidates: [ProductSuggestion]?
    var meijerSearchURL: String?

    var stableCode: String { upc ?? normalizedCode ?? rawCode ?? UUID().uuidString }

    enum CodingKeys: String, CodingKey {
        case upc, quantity, status, source, product, suggestion, candidates
        case rawCode = "raw_code"
        case normalizedCode = "normalized_code"
        case receiptDescription = "receipt_description"
        case lineTotal = "line_total"
        case unitPrice = "unit_price"
        case meijerSearchURL = "meijer_search_url"
    }
}

struct ReceiptResolution: Codable {
    var items: [ReceiptLine]
}

struct ImportResult: Codable {
    var receiptID: Int
    var importedItems: Int
    var resolvedItems: Int
    var unresolvedItems: Int

    enum CodingKeys: String, CodingKey {
        case receiptID = "receipt_id"
        case importedItems = "imported_items"
        case resolvedItems = "resolved_items"
        case unresolvedItems = "unresolved_items"
    }
}

struct ProductDraft: Codable, Hashable {
    var upc: String
    var name: String = ""
    var brand: String = ""
    var size: String = ""
    var category: String = ""
    var unit: String = ""
    var notes: String = ""
    var defaultLocation: String = ""
    var lookupSource: String = "manual"

    init(upc: String = "", suggestion: ProductSuggestion? = nil) {
        self.upc = upc
        name = suggestion?.name ?? ""
        brand = suggestion?.brand ?? ""
        size = suggestion?.size ?? ""
        category = suggestion?.category ?? ""
        unit = suggestion?.unit ?? ""
        notes = suggestion?.notes ?? ""
        defaultLocation = suggestion?.defaultLocation ?? ""
        lookupSource = suggestion?.lookupSource ?? "manual"
    }

    var payload: [String: String?] {
        [
            "upc": clean(upc),
            "name": clean(name),
            "brand": clean(brand),
            "size": clean(size),
            "category": clean(category),
            "unit": clean(unit),
            "notes": clean(notes),
            "default_location": clean(defaultLocation),
            "lookup_source": clean(lookupSource) ?? "manual",
        ]
    }

    var canSave: Bool {
        !upc.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty &&
        !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    private func clean(_ value: String) -> String? {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }
}
