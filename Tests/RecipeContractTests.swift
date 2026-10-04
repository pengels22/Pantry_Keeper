import Foundation

// Transport double: compile the actual recipe API methods against backend-generated fixtures.
@MainActor
final class RecipeTestTransport {
    var expected: [(String, String, String)] = []
    var bodies: [[String: Any]] = []
    var timeouts: [TimeInterval] = []
}

@MainActor
struct PantryAPI {
    let transport: RecipeTestTransport
    func request<T: Decodable>(_ path: String, method: String = "GET", body: Data? = nil,
                              contentType: String? = nil, timeout: TimeInterval = 90) async throws -> T {
        try require(!transport.expected.isEmpty, "Unexpected extra request: \(path)")
        let (expectedPath, expectedMethod, fixture) = transport.expected.removeFirst()
        try require(path == expectedPath && method == expectedMethod, "Incorrect recipe endpoint or HTTP method: \(path)")
        if let body {
            try require(contentType == "application/json", "Missing JSON content type")
            transport.bodies.append(try JSONSerialization.jsonObject(with: body) as! [String: Any])
        }
        transport.timeouts.append(timeout)
        return try JSONDecoder().decode(T.self, from: fixtureData(fixture))
    }
}

struct ContractFailure: Error { let message: String }
func require(_ condition: @autoclosure () -> Bool, _ message: String) throws {
    if !condition() { throw ContractFailure(message: message) }
}
func fixtureData(_ name: String) throws -> Data {
    try Data(contentsOf: URL(fileURLWithPath: "Tests/Fixtures/\(name).json"))
}

@main
struct RecipeContractTests {
    @MainActor
    static func main() async throws {
        let decoder = JSONDecoder()
        let proposal = try decoder.decode(RecipeProposal.self, from: fixtureData("proposal"))
        let json = try JSONSerialization.jsonObject(with: JSONEncoder().encode(proposal)) as! [String: Any]
        let ingredient = (json["ingredients"] as! [[String: Any]])[0]
        try require(Set(ingredient.keys) == Set(["inventory_id", "name", "amount", "unit", "notes"]), "Response-only proposal fields leaked into request")
        try require(proposal.ingredients[0].name == "Chicken Breast", "Server product identity was not decoded")
        let completed = try decoder.decode(CookingSession.self, from: fixtureData("completed"))
        try require(completed.status == "completed" && !completed.canCancel, "Completed session status was not decoded")
        try require(completed.items[0].consumed_amount == 0, "Zero actual use was not retained")
        let reserved = try decoder.decode(CookingSession.self, from: fixtureData("reserved"))
        try require(reserved.canCancel && reserved.items[0].consumed_amount == nil, "Reserved session null fields failed")
        try require(RecipeNumbers.parse("1,5") == 1.5 && RecipeNumbers.parse("0") == 0, "Localized quantities failed")
        for bad in ["-1", "nan", "inf", "", "1.2.3"] {
            try require(RecipeNumbers.parse(bad) == nil, "Invalid quantity accepted: \(bad)")
        }

        let transport = RecipeTestTransport()
        let api = PantryAPI(transport: transport)
        transport.expected = [("/api/recipes/chat", "POST", "chat")]
        let history = (0..<30).map { RecipeChatMessage(role: "user", content: "Message \($0)") }
        let chat = try await api.recipeChat(message: "Dinner please", history: history)
        let encodedHistory = transport.bodies.last!["history"] as! [[String: Any]]
        try require(encodedHistory.count == 20 && encodedHistory[0]["content"] as? String == "Message 10", "Chat history bounds failed")
        try require(encodedHistory[0]["id"] == nil, "Local chat UUID leaked into request")
        try require(chat.proposal != nil && transport.timeouts.last! >= 480, "Chat proposal or tool-loop timeout failed")

        transport.expected = [("/api/recipes/session", "POST", "selected")]
        let session = try await api.selectRecipe(proposal)
        try require(transport.bodies.last!["proposal"] != nil && session.status == "selected", "Recipe selection payload failed")
        transport.expected = [("/api/recipes/\(session.id)/reserve", "POST", "reserved")]
        _ = try await api.reserveRecipe(session.id)
        transport.expected = [("/api/recipes/\(session.id)/commit", "POST", "completed")]
        _ = try await api.finishRecipe(session.id, usage: reserved.items.map {
            RecipeActualUsage(inventory_id: $0.id, amount: 0, unit: $0.requested_unit)
        })
        let commit = transport.bodies.last!
        try require(commit["confirmed"] as? Bool == true, "Explicit confirmation missing")
        let usage = commit["actual_usage"] as! [[String: Any]]
        try require(usage.count == reserved.items.count && usage[0]["amount"] as? Double == 0, "Actual use payload failed")
        try require(Set(usage[0].keys) == Set(["inventory_id", "amount", "unit"]), "Invalid actual use fields")
        transport.expected = [("/api/recipes/\(session.id)/cancel", "POST", "cancelled")]
        _ = try await api.cancelRecipe(session.id)
        transport.expected = [("/api/recipes/\(session.id)", "GET", "completed"), ("/api/recipes/sessions", "GET", "sessions")]
        _ = try await api.recipeSession(session.id)
        _ = try await api.recipeSessions()
        transport.expected = [("/api/inventory?limit=200&offset=0", "GET", "inventory")]
        let items = try await api.recipeInventory()
        try require(items.count == 3, "Inventory decoding failed")
        var item = items[0]
        item.package_quantity = 1; item.package_size = 4; item.package_unit = "each"
        transport.expected = [("/api/inventory/\(item.id)/measurements", "PUT", "inventory-item")]
        _ = try await api.saveRecipeMeasurements(item, quantity: 3, unit: "each")
        try require(transport.bodies.last!["package_size"] as? Double == 4, "Package metadata was lost")
        try require(transport.expected.isEmpty, "Expected requests were not made")
        print("Recipe Swift request/response contracts passed.")
    }
}
