import Foundation

struct RecipeInventoryItem: Codable, Identifiable {
    var inventory_id: Int
    var name: String
    var usable_quantity: Double?
    var usable_unit: String?
    var available_quantity: Double?
    var package_quantity: Double?
    var package_size: Double?
    var package_unit: String?
    var reserved_quantity: Double
    var measurement_required: Bool
    var id: Int { inventory_id }
}

struct RecipeIngredient: Codable, Identifiable {
    var inventory_id: Int
    var name: String
    var amount: Double
    var unit: String
    var notes: String
    var id: Int { inventory_id }
}

// Only request fields are encoded. The server adds availability fields to proposals.
struct RecipeProposal: Codable {
    var recipe: String
    var instructions: String
    var ingredients: [RecipeIngredient]
}

struct RecipeChatMessage: Codable, Identifiable {
    var role: String
    var content: String
    var id: UUID = UUID()
    enum CodingKeys: String, CodingKey { case role, content }
}

struct RecipeChatResponse: Decodable {
    var reply: String
    var proposal: RecipeProposal?
}

struct RecipeSessionSummary: Decodable, Identifiable {
    var id: Int
    var title: String
    var status: String
}

struct CookingSession: Decodable, Identifiable {
    var id: Int
    var title: String
    var status: String
    var instructions: String
    var items: [CookingIngredient]
    var canCancel: Bool { ["planning", "selected", "reserved"].contains(status) }
}

struct CookingIngredient: Decodable, Identifiable {
    var inventory_id: Int
    var name: String
    var requested_amount: Double
    var requested_unit: String
    var consumed_amount: Double?
    var consumed_unit: String?
    var notes: String?
    var id: Int { inventory_id }
}

struct RecipeActualUsage: Encodable {
    var inventory_id: Int
    var amount: Double
    var unit: String
}

struct RecipeMeasurements: Encodable {
    var package_quantity: Double?
    var package_size: Double?
    var package_unit: String?
    var usable_quantity: Double
    var usable_unit: String
}

enum RecipeNumbers {
    static let units = ["each", "g", "kg", "oz", "lb", "ml", "L", "tsp", "tbsp", "cup", "pint", "quart", "gallon"]

    static func display(_ value: Double) -> String {
        String(format: "%.15g", locale: Locale(identifier: "en_US_POSIX"), value)
    }

    static func parse(_ text: String) -> Double? {
        let normalized = text.trimmingCharacters(in: .whitespacesAndNewlines).replacingOccurrences(of: ",", with: ".")
        guard let value = Double(normalized), value.isFinite, value >= 0 else { return nil }
        return value
    }
}
