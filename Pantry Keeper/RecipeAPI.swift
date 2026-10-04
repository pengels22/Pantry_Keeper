import Foundation

extension PantryAPI {
    func recipeInventory() async throws -> [RecipeInventoryItem] {
        var all: [RecipeInventoryItem] = []
        var offset = 0
        while true {
            let page: [RecipeInventoryItem] = try await request("/api/inventory?limit=200&offset=\(offset)")
            all.append(contentsOf: page)
            if page.count < 200 { return all }
            offset += 200
        }
    }

    func recipeChat(message: String, history: [RecipeChatMessage]) async throws -> RecipeChatResponse {
        struct Payload: Encodable { var message: String; var history: [RecipeChatMessage] }
        return try await request("/api/recipes/chat", method: "POST",
            body: JSONEncoder().encode(Payload(message: message, history: history.suffix(20).map { RecipeChatMessage(role: $0.role, content: String($0.content.prefix(10000))) })),
            contentType: "application/json", timeout: 600)
    }

    func recipeSessions() async throws -> [RecipeSessionSummary] {
        try await request("/api/recipes/sessions")
    }

    func recipeSession(_ id: Int) async throws -> CookingSession {
        try await request("/api/recipes/\(id)")
    }

    func selectRecipe(_ proposal: RecipeProposal) async throws -> CookingSession {
        struct Payload: Encodable { var proposal: RecipeProposal }
        return try await request("/api/recipes/session", method: "POST",
            body: JSONEncoder().encode(Payload(proposal: proposal)), contentType: "application/json")
    }

    func reserveRecipe(_ id: Int) async throws -> CookingSession {
        try await request("/api/recipes/\(id)/reserve", method: "POST")
    }

    func cancelRecipe(_ id: Int) async throws -> CookingSession {
        try await request("/api/recipes/\(id)/cancel", method: "POST")
    }

    func finishRecipe(_ id: Int, usage: [RecipeActualUsage]) async throws -> CookingSession {
        struct Payload: Encodable { var confirmed = true; var actual_usage: [RecipeActualUsage] }
        return try await request("/api/recipes/\(id)/commit", method: "POST",
            body: JSONEncoder().encode(Payload(actual_usage: usage)), contentType: "application/json")
    }

    func saveRecipeMeasurements(_ item: RecipeInventoryItem, quantity: Double, unit: String) async throws -> RecipeInventoryItem {
        try await request("/api/inventory/\(item.id)/measurements", method: "PUT",
            body: JSONEncoder().encode(RecipeMeasurements(package_quantity: item.package_quantity, package_size: item.package_size, package_unit: item.package_unit, usable_quantity: quantity, usable_unit: unit)),
            contentType: "application/json")
    }
}
