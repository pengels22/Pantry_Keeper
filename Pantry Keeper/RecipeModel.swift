import Combine
import Foundation

@MainActor
final class RecipeModel: ObservableObject {
    @Published var inventory: [RecipeInventoryItem] = []
    @Published var sessions: [RecipeSessionSummary] = []
    @Published var messages: [RecipeChatMessage] = []
    @Published var proposal: RecipeProposal?
    @Published var session: CookingSession?
    @Published var actualAmounts: [Int: String] = [:]
    @Published var busy = false
    @Published var error: String?

    func refresh(api: PantryAPI) async {
        await perform {
            async let inventory = api.recipeInventory()
            async let sessions = api.recipeSessions()
            self.inventory = try await inventory
            self.sessions = try await sessions
            if let id = self.session?.id {
                self.apply(try await api.recipeSession(id), resetAmounts: false)
            }
        }
    }

    func chat(_ message: String, api: PantryAPI) async -> Bool {
        var sent = false
        await perform {
            let response = try await api.recipeChat(message: message, history: self.messages)
            self.messages.append(RecipeChatMessage(role: "user", content: message))
            self.messages.append(RecipeChatMessage(role: "assistant", content: response.reply))
            self.proposal = response.proposal
            sent = true
        }
        return sent
    }

    func select(api: PantryAPI) async {
        guard let proposal else { return }
        await perform {
            self.apply(try await api.selectRecipe(proposal))
            self.proposal = nil
            self.sessions = try await api.recipeSessions()
        }
    }

    func open(_ id: Int, api: PantryAPI) async {
        await perform { self.apply(try await api.recipeSession(id)) }
    }

    func reserve(api: PantryAPI) async {
        guard let session, session.status == "selected" else { return }
        await mutateSession(api: api, id: session.id) { try await api.reserveRecipe(session.id) }
    }

    func cancel(api: PantryAPI) async {
        guard let session, session.canCancel else { return }
        await mutateSession(api: api, id: session.id) { try await api.cancelRecipe(session.id) }
    }

    var actualUsage: [RecipeActualUsage]? {
        guard let session, session.status == "reserved", !session.items.isEmpty else { return nil }
        var result: [RecipeActualUsage] = []
        for item in session.items {
            guard let amount = RecipeNumbers.parse(actualAmounts[item.id] ?? "") else { return nil }
            result.append(RecipeActualUsage(inventory_id: item.id, amount: amount, unit: item.requested_unit))
        }
        return result
    }

    func finish(api: PantryAPI) async {
        guard let session, let usage = actualUsage else { return }
        await mutateSession(api: api, id: session.id) { try await api.finishRecipe(session.id, usage: usage) }
    }

    func saveMeasurements(_ item: RecipeInventoryItem, quantity: Double, unit: String, api: PantryAPI) async -> Bool {
        var saved = false
        await perform {
            let updated = try await api.saveRecipeMeasurements(item, quantity: quantity, unit: unit)
            if let index = self.inventory.firstIndex(where: { $0.id == item.id }) { self.inventory[index] = updated }
            self.proposal = nil // Proposals must be regenerated against the new measurements.
            saved = true
        }
        return saved
    }

    private func apply(_ session: CookingSession, resetAmounts: Bool = true) {
        let reset = resetAmounts || self.session?.id != session.id || self.session?.status != session.status
        self.session = session
        if reset {
            actualAmounts = Dictionary(uniqueKeysWithValues: session.items.map { ($0.id, RecipeNumbers.display($0.requested_amount)) })
        }
    }

    // If a response is lost, reload authoritative state rather than retrying a stock mutation.
    private func mutateSession(api: PantryAPI, id: Int, action: () async throws -> CookingSession) async {
        await perform {
            do { self.apply(try await action()) }
            catch {
                if let current = try? await api.recipeSession(id) { self.apply(current, resetAmounts: false) }
                throw error
            }
            self.inventory = try await api.recipeInventory()
            self.sessions = try await api.recipeSessions()
        }
    }

    private func perform(_ action: () async throws -> Void) async {
        guard !busy else { return }
        busy = true
        error = nil
        defer { busy = false }
        do { try await action() }
        catch { self.error = error.localizedDescription }
    }
}
